import json
import re
import subprocess
import requests
from bs4 import BeautifulSoup as bs

KEYWORDS = ['announced', 'new', 'product', 'launch', 'launched', 'release', 'version']
LLAMA_MODEL = "llama3.2"

def get_data_from_url(url):
    headers = {
        'User-Agent': 'manuelisaacgarcia@gmail.com',
        'Accept-Encoding': 'tgzip, deflate',
        'Host': 'www.sec.gov',
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors
        if response.content:  # Check if the response content is not empty
            return response
        else:
            print("No content received from the server.")
            return None
    except requests.RequestException as e:
        return None

def query_ollama(model, prompt):
    """Runs an Ollama query using a specified model and prompt."""

    # Execute the Ollama command and capture output
    result = subprocess.run(
        f'ollama run {model} "{prompt}"'.split(' '),
        capture_output=True, text=True, encoding='utf-8'#False if true gives you an error
    )
    return result.stdout.strip()

def get_8k_filing_content(filing_details_url):
    try:
        response = get_data_from_url(filing_details_url)
        response_soup = bs(response.text, 'html.parser')
        table = response_soup.find('table')
        links = query_ollama(LLAMA_MODEL, f"Give me a comma separated list of all the links in this table. "
                                                 f"make sure you include http://www.sec.gov/ before all the links "
                                                 f"Reply with just the links and nothing else."
                                                 f"if you can't find them, just reply \"NA\" and nothing else"
                                                 f"\n\n {table}")

        if 'NA' in links:
            return None

        text_excerpts = []
        for link in links.split(','):
            if '.jpg' in link: continue

            content_8k_filing = get_data_from_url(link)
            file_content = bs(content_8k_filing.text, 'html.parser')
            file_text = file_content.text.strip().replace('\n', '')

            for keyword in KEYWORDS:
                meaningful_text_excerpts = find_surrounding_text(file_text, keyword)
                text_excerpts.append(meaningful_text_excerpts)
        return text_excerpts

    except Exception as e:
        return None

def find_surrounding_text(text, keyword, window_size=80):
    import re

    # Find all occurrences of the keyword in the text
    matches = [match.start() for match in re.finditer(re.escape(keyword), text)]

    # Extract surrounding text for each match
    surrounding_texts = []
    for match in matches:
        start = max(0, match - window_size)
        end = min(len(text), match + len(keyword) + window_size)
        surrounding_texts.append(text[start:end])

    return surrounding_texts

def write_to_output_csv(str):
    with open('output.csv', 'a') as csv:
        csv.write(str)


if __name__ == "__main__":

    company_cik_url = "https://www.sec.gov/files/company_tickers.json"
    company_cik_values_content = get_data_from_url(company_cik_url)
    company_cik_values_json = company_cik_values_content.json()

    # Making a dictionary of company tickers as keys and ciks as values
    company_ciks_dict = {}
    for key, value in company_cik_values_json.items():
        company_ciks_dict[value['ticker']] = value['cik_str']

    with open('output.csv', 'w') as f:
         f.write('company_name | stock_name | filing_time | new_product | product_description\n')

    # Iterating through every company in our dictionary
    for ticker, cik in company_ciks_dict.items():

        print(f"Getting 8k filings for {ticker}\n")
        filings_8k_url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
                          f"&type=8-K&count=300&output=atom")
        filing_entries_content = get_data_from_url(filings_8k_url)
        filing_entries_xml = filing_entries_content.text

        pattern = r"<entry>.*?</entry>"
        entries_8k_list = re.findall(pattern, filing_entries_xml, re.DOTALL)
        print(f'found {len(entries_8k_list)} entries')

        pattern = f"<company-info>.*?</company-info>"
        company_info = re.findall(pattern, filing_entries_xml, re.DOTALL)

        company_name = query_ollama(LLAMA_MODEL, f"Tell me the company name of this 8-K filing. "
                                                 f"Reply with just the company name or \"NA\" "
                                                 f"if you can't find it. "
                                                 f"\n\n {company_info}")

        for entry in entries_8k_list:

            summary_items_pattern = r"<items-desc>.*?</items-desc>"
            new_items = re.search(summary_items_pattern, entry, re.DOTALL)
            new_items = new_items.group(0)

            # if items 8.01 and 9.01 or 8.02 are not present in the 8k,
            # it's very unlikely that there is a product launch in the filing
            # so we skip them
            if '8.01' not in new_items and '9.01' not in new_items and '8.02' not in new_items:
                continue

            # Find the filing date
            filing_date = query_ollama(LLAMA_MODEL,  f"Extract the filing date of this 8-K filing from the "
                                                            f"<filing-date> tag. "
                                                            f"Reply only with the text inside the tags "
                                                            f"\"NA\" if you can't find it. "
                                                            f"\n\n {entry}")
            if filing_date == 'NA' or company_name == 'NA':
                continue

            #  Get the link to the actual 8-K filing from the entry
            filing_detail_link = query_ollama(LLAMA_MODEL, f"This is text in xml format. Please extract the text "
                                                           f"inside of the 'filing-href' tag and give it back to me. "
                                                           f"Reply only with the text inside the tags\n\n {entry}")

            # Get the relevant content that we want to analize
            filing_content = get_8k_filing_content(filing_detail_link)
            if filing_content is None or not filing_content:
                continue

            # Analize the excerpts that we found and could contain new products
            for text_excerpts in filing_content:
                product_launches_lookup_prompt =  f"Does this 8-K form filing excerpt for {company_name} "\
                                                  f"say that a product is being launched or announced? "\
                                                  f"Only reply with yes or no, do not say anything else " \
                                                  f"\n\n {text_excerpts}"

                product_lookup = query_ollama(LLAMA_MODEL, product_launches_lookup_prompt)
                if product_lookup == 'no' or product_lookup == 'No' or product_lookup == 'no.':
                    continue

                # Find the names of the products being launched
                product_name_lookup_prompt = f"Take a look at this 8-k and tell me the full name of the products being launched. "\
                                             f"Reply only with the full name of the product and nothing else. "\
                                             f" if there are multiple, separate them with commas. "\
                                             f"If there aren't any, just reply NA and nothing else\n\n {text_excerpts}"
                product_names = query_ollama(LLAMA_MODEL, product_name_lookup_prompt)

                if product_names == 'NA':
                    continue

                elif ',' in product_names:
                    product_names = product_names.split(',')
                    # Trick to remove duplicates
                    product_names = list(set(product_names))
                    for product in product_names:
                        product_description_prompt = f"Write a very short description of the product: "\
                                                     f"{product}. If {product} isn't a product made "\
                                                     f"by {company_name}, then reply \"NA\" and nothing else"
                        product_description = query_ollama(LLAMA_MODEL, product_description_prompt)
                        if product_description == 'NA':
                           continue

                        write_to_output_csv(f'{company_name} | {ticker} | {filing_date} | {product} | {product_description}\n')
                else:
                    product = product_names
                    product_description_prompt = f"Write a very short description of the product: " \
                                                 f"{product}. If {product} isn't a product made " \
                                                 f"by {company_name}, then reply \"NA\" and nothing else"
                    product_description = query_ollama(LLAMA_MODEL, product_description_prompt)
                    if product_description == 'NA':
                        continue

                    write_to_output_csv(f'{company_name} | {ticker} | {filing_date} | {product} | {product_description}\n')




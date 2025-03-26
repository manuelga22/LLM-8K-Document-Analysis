import json
import pprint
import re
import subprocess
import time

import requests
from bs4 import BeautifulSoup as bs



# LLAMA_MODEL = "llama3.2"
LLAMA_MODEL = "deepseek-r1:8b"
# CHAR_LIMIT = 2400

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
        link = query_ollama(LLAMA_MODEL, f"Find the link in this html table to the 8K filing. "
                                                f"Reply with just the link and nothing else."
                                                f"if you can't find it, just reply \"NA\" and nothing else"
                                                f"\n\n {table}")
        if 'NA' in link:
            return None

        content_8k_filing = get_data_from_url(link)
        file_content = bs(content_8k_filing.text, 'html.parser')

        # We truncate it bc sometimes the file is too long and can't fit into a llama prompt
        file = truncate_8k_filing(file_content.text.strip().replace('\n',' '))

        is_8k_filing = query_ollama(LLAMA_MODEL, f"does this text look like an 8-K filing? "
                                                         f"just reply yes or no and nothing else \n\n {file}")

        if is_8k_filing == 'no':
            return None

        return file

    except Exception as e:
        return None

def truncate_8k_filing(filing):
    try:
        pattern = r'FORM\s+8-K.*$'
        match = re.search(pattern, filing, re.DOTALL)
        return match.group(0)
    except Exception as e:
        print(f"could not truncate filing")
        return filing

if __name__ == "__main__":

    company_cik_url = "https://www.sec.gov/files/company_tickers.json"
    company_cik_values_content = get_data_from_url(company_cik_url)
    company_cik_values_json = company_cik_values_content.json()

    # Making a dictionary of company tickers to cik
    company_ciks_dict = {}
    for key, value in company_cik_values_json.items():
        company_ciks_dict[value['ticker']] = value['cik_str']

    with open('output.csv', 'w') as f:
         f.write('company_name | stock_name | filing_time | new_product | product_description\n')

    
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
            if '8.01' not in new_items and '9.01' not in new_items and '8.02' not in new_items:
                continue

            filing_date = query_ollama(LLAMA_MODEL, f"Extract the filing date of this 8-K filing from the "
                                                           f"<filing-date> tag. "
                                                            f"Reply with just the date in the format yyyy-mm-dd or "
                                                            f"\"NA\" if you can't find it. "
                                                            f"\n\n {entry}")


            if filing_date == 'NA' or company_name == 'NA':
                continue

            filing_detail_link = query_ollama(LLAMA_MODEL, f"This is text in xml format. Please extract the text "
                                                           f"inside of the 'filing-href' tag and give it back to me. "
                                                           f"Reply only with the text inside the tags\n\n {entry}")
            filing_content = get_8k_filing_content(filing_detail_link)
            if filing_content is None:
                continue

            print(company_name, filing_date)
            product_launches_lookup_prompt = f"Look in this 8-K form filing to see if there are new product launches. "\
                                             f"If there aren't any, just reply \"NA\" and nothing else" \
                                             f"keep the answer short. " \
                                             f"\n\n {filing_content}"

            product_lookup = query_ollama(LLAMA_MODEL, product_launches_lookup_prompt)

            if product_lookup == 'NA':
                continue

            product_name_lookup_prompt = f"Take a look at this text and tell me the name of the product being launched. "\
                                          f"If there aren't any, just reply NA and nothing else\n\n {product_lookup}"
            product_name = query_ollama(LLAMA_MODEL, product_name_lookup_prompt)
            if product_name == 'NA':
                continue

            product_description_prompt = f"Write a very short description of the product: "\
                                         f"{product_name}. If {product_name} isn't a product made "\
                                         f"by {company_name}, then reply \"NA\" and nothing else"
            product_description = query_ollama(LLAMA_MODEL, product_description_prompt)
            if product_description == 'NA':
               continue


            with open('output.csv', 'a') as f:
                f.write(f'{company_name} | {ticker} | {filing_date} | {product_name} | {product_description}\n')


from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI
from neo4j import GraphDatabase  # Neo4j


import arxiv
import requests
import re


from marker.converters.pdf import PdfConverter
from io import BytesIO


from marker.models import create_model_dict

from time import sleep


BASE = "https://api.semanticscholar.org/graph/v1"
API_KEY = "CENSORED"  
HEADERS = {"x-api-key": API_KEY} 

AZURE_STORAGE_CONNECTION_STRING = "CENSORED"
PDF_CONTAINER = "mcontainer"
MD_CONTAINER = "mdcontainer"

URI = "bolt://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "CENSORED"
DATABASE = "paperconnection"

endpoint = "CENSORED"
model_name = "gpt-4o-mini"
deployment = "gpt-4o-mini"
subscription_key = "CENSORED"
api_version = "2024-12-01-preview"

blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
pdf_container_client = blob_service_client.get_container_client(PDF_CONTAINER)
md_container_client = blob_service_client.get_container_client(MD_CONTAINER)

driver = GraphDatabase.driver(URI, auth=(USERNAME, PASSWORD))

client = AzureOpenAI(
    api_key=subscription_key,
    api_version=api_version,
    azure_endpoint=endpoint,
)


arxiv_ids=[]
# azure_arxiv_ids=[]
def get_arxiv_papers(arxiv_ids,pdf_container_client):
# Perform search
    count=0
    search = arxiv.Search(
    query='"self driving cars" OR "autonomous vehicles"',
    id_list=[],
    sort_by=arxiv.SortCriterion.Relevance,
    sort_order=arxiv.SortOrder.Descending,
    )

    container_client = blob_service_client.get_container_client("mcontainer")


    for result in search.results():
        print(f"Paper Name: {result.title}")
        print(f"Date Released: {result.published}")
        print(f"Paper link: {result.pdf_url}")

        # Download PDF
        filter = r"^(\d{4}\.\d{4,5})(?:v\d+)?$"
        # print(result.get_short_id())
        check = re.match(filter, str(result.get_short_id()))
        # azure_arxiv_ids.append(check.group(1))
        # print(check)
        if check:
            print (f"Valid Arxiv ID: {check.group(1)}")
            arxiv_ids.append(check.group(1))
        blob_client = container_client.get_blob_client(blob = f"{check.group(1)}.pdf")

        r = requests.get(result.pdf_url, stream=True)
        blob_client.upload_blob(data=r.raw, overwrite=True)

        # print(f"Saved PDF to Azure: {a}.pdf")

        count= count + 1
        if count == 1:
            break  # Download only one paper for testing
    print(f"Downloaded {count} papers to: mcontainer")

    return arxiv_ids

import re

def extract_marker_sections(result):
    """
    Extract specific sections from marker MarkdownOutput result.
    Keeps abstract/introduction/conclusion/summary and references.
    """
    final_md = ""
    lines = result.markdown.split('\n')

    current_section = ""
    current_content = []
    target_sections = ("abstract", "introduction", "conclusion", "summary")
    reference_sections = ("references", "bibliography", "works cited")

    def parse_references(ref_lines):
        items = []
        cur = None
        starter = re.compile(r'^\s*(?:\[(\d+)\]|(\d+)[\.\)]?)\s+(.*\S)?\s*$')
        for raw in ref_lines:
            line = raw.rstrip()
            if not line.strip():
                continue
            m = starter.match(line)
            if m:
                if cur:
                    items.append(cur.strip())
                cur = (m.group(3) or "").strip()
            else:
                if cur is None:
                    cur = line.strip()
                else:
                    cur += " " + line.strip()
        if cur:
            items.append(cur.strip())
        return items

    def flush_section(sec, content_lines):
        nonlocal final_md
        content = "\n".join(content_lines).strip()
        if not content:
            return
        low = sec.lower()
        if any(section in low for section in target_sections):
            final_md += f"## {sec}\n\n{content}\n\n"
        elif any(section in low for section in reference_sections):
            refs = parse_references(content_lines)
            if refs:
                final_md += f"## {sec}\n\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(refs)) + "\n\n"

    for line in lines:
        if line.startswith('#'):
            if current_section:
                flush_section(current_section, current_content)
            current_section = line.lstrip('#').strip()
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        flush_section(current_section, current_content)

    return final_md.strip()


def convert_pdf_to_markdown_from_azure():
    print("Converting PDFs from Azure Blob to Markdown...")

    model_dict = create_model_dict()
    converter = PdfConverter(model_dict)
    # converter = RawConverter(model_dict)

    for blob in pdf_container_client.list_blobs(name_starts_with=""):
        pdf_name = blob.name
        # Skip if already exists
        md_name = pdf_name.replace(".pdf", ".md")
        if any(md_blob.name == pdf_name for md_blob in md_container_client.list_blobs(name_starts_with="")):
            print(f" Skipping already converted: {pdf_name}")
            continue

        print(f"↪ Converting {pdf_name}...")

        # Download PDF
        blob_client = pdf_container_client.get_blob_client(pdf_name)
        pdf_bytes = blob_client.download_blob().readall()

        result = converter(BytesIO(pdf_bytes))
        markdown_text = extract_marker_sections(result)

        # Upload Markdown
        md_blob_client = md_container_client.get_blob_client(md_name)
        md_blob_client.upload_blob(markdown_text, overwrite=True)

        print(f"Saved Markdown to Azure: {md_name}")

    print(" All PDFs processed.\n")

def add_paper(response_data,arxiv_id):
    print(response_data.get("title"))
    print(response_data.get("url"))
    print(response_data.get("publicationTypes"))
    print(response_data.get("publicationDate"))
    print(response_data.get("openAccessPdf"))
    print(response_data.get("paperId"))
    print(arxiv_id)

    summary = driver.execute_query("""
        CREATE (a:Paper {PaperId: $name, ArxivId: $arxivid,Title: $title, PublicationDate: $publicationDate})
        """,
        name=response_data.get("paperId"), arxivid=arxiv_id, title=response_data.get("title"), publicationDate=response_data.get("publicationDate"),
        database_="paperconnection",
    ).summary
    print("Created {nodes_created} nodes in {time} ms.".format(
        nodes_created=summary.counters.nodes_created,
        time=summary.result_available_after
    ))

    return arxiv_id
def query_semantic_api(arxiv_ids):
    for arxiv_id in arxiv_ids:

        # Define the API endpoint URL
        url = f"http://api.semanticscholar.org/graph/v1/paper/ARXIV:{arxiv_id}"

        # Define the query parameters
        query_params = {"fields": "title,url,publicationTypes,publicationDate,openAccessPdf"}
        

        # Directly define the API key (Reminder: Securely handle API keys in production environments)
        api_key = "CENSORED"  # Replace with the actual API key

        # Define headers with API key
        headers = {"x-api-key": api_key}

        # Send the API request
        response = requests.get(url, params=query_params, headers=headers)

        # Check response status
        if response.status_code == 200:
            response_data = response.json()
        # Process and print the response data as needed
            print(response_data)
            arxiv_id = add_paper(response_data, arxiv_id)
            output,arxiv_id=get_references_from_paper(arxiv_id)
            references,arxiv_id =(cross_check_references(output,arxiv_id))
            references=llm_filter_references(references)
            # print(arxiv_id)
            arxiv_id= add_references(references,arxiv_id)
            raw,arxiv_id=ask_llm(arxiv_id,md_container_client)
            upload_hypotheis_and_reasoning(raw, arxiv_id)


        else:
            print(f"Request failed with status code {response.status_code}: {response.text}")



    return response_data, arxiv_id

def get_references_from_paper(arpaper_id):

    sleep(3)


    paperid = arpaper_id
    # Define the API endpoint URL
    url = f"http://api.semanticscholar.org/graph/v1/paper/ARXIV:{paperid}/references?limit=1000&offset={0}"


    # Define the query parameters
    query_params = {"fields": "title,authors,url,publicationTypes,publicationDate,openAccessPdf"}

    # "references.paperId","references.title","references.year"

    # Directly define the API key (Reminder: Securely handle API keys in production environments)
    api_key = "CENSORED"  # Replace with the actual API key

    # Define headers with API key
    headers = {"x-api-key": api_key}

    # Send the API request
    response = requests.get(url, params=query_params, headers=headers)

    # Check response status
    if response.status_code == 200:
        response_data = response.json()
    # Process and print the response data as needed
        # print(response_data["data"])
        # sleep(2)
    else:
        print(f"Request failed with status code {response.status_code}: {response.text}")



    output = []
    if response_data["data"] == None:
        print(f"No references found for paperId: {paperid}")
        return False
    else:
        # output.append({"url_main_paper" : paper.get("url", "N/A")})
        for i, ref in enumerate(response_data["data"], start=1):
            paper = ref["citedPaper"]
            output.append({
                "index": i,
                "title": paper.get("title", "N/A"),
                "authors": [a["name"] for a in paper.get("authors", [])],
                "paperId": paper.get("paperId", "N/A"),
                "publicationDate": paper.get("publicationDate", "N/A")
            })
    print(f"Haresh {output}")
    return output, arpaper_id
import re, unicodedata
from copy import deepcopy

# ---------- helpers ----------
def norm(s):
    s = ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def clean_span_and_md(s: str) -> str:
    # remove trailing "- <span ...>" and inline spans/links/italics/backticks
    s = re.sub(r"-\s*<span[^>]*></span>\s*$", "", s)
    s = re.sub(r"<span[^>]*>", "", s).replace("</span>", "")
    s = re.sub(r"\*([^*]+)\*", r"\1", s)       # remove *emphasis*
    s = s.replace("`", "")                     # stray backticks
    return s.strip()

# ---------- 1) parse references -> cleaned_refs ----------
def parse_references_list(references):
    CITE_QUOTE = r'["“”«»]'  # basic quote chars
    cleaned_refs = []

    for ref in references:
        s = clean_span_and_md(ref)

        # [n] and body
        m = re.match(r"\[(\d+)\]\s*(.*)", s)
        if not m:
            continue
        idx = int(m.group(1))
        body = m.group(2).strip()

        # quoted title
        m_title = re.search(fr"{CITE_QUOTE}(.*?){CITE_QUOTE}", body)
        if m_title:
            title = m_title.group(1).strip()
            authors_part = body[:m_title.start()].strip(" ,;-")
        else:
            # fallback: title until first period or " in "
            cut = len(body)
            m_in = re.search(r"\bin\b", body)
            if m_in: cut = min(cut, m_in.start())
            m_dot = re.search(r"\.", body)
            if m_dot: cut = min(cut, m_dot.start())
            title = body[:cut].strip(" ,;-")
            authors_part = ""

        # handle em-dash repeat "——," -> ignore (no author list expansion here)
        authors_part = authors_part.replace("——", "").strip(" ,;-")

        # split authors
        authors = [a.strip().replace("’", "'") for a in authors_part.split(",") if a.strip()]

        if title:
            cleaned_refs.append({"index": idx, "title": title, "authors": authors})

    # keep only plausible references (title must exist)
    cleaned_refs = [r for r in cleaned_refs if r["title"]]
    # sort by index
    cleaned_refs.sort(key=lambda d: d["index"])
    return cleaned_refs

# ---------- 2) merge into your output ----------
def merge_simple(cleaned_refs, output_refs):
    by_title = {norm(r["title"]): r for r in cleaned_refs if r.get("title")}
    used = set()
    merged = []
    for it in output_refs:
        # drop note-like rows (no authors and no paperId)
        if not it.get("authors") and not it.get("paperId"):
            continue
        key = norm(it.get("title",""))
        if key in by_title:
            cr = by_title[key]; used.add(key)
            it = {**it, "title": cr["title"], "authors": cr["authors"], "index": cr["index"]}
        merged.append(it)
    # add any missing from cleaned
    for k, cr in by_title.items():
        if k not in used:
            merged.append({
                "index": cr["index"],
                "title": cr["title"],
                "authors": cr["authors"],
                "paperId": None
            })
    return sorted(merged, key=lambda d: d.get("index", 10**9))



def cross_check_references(output, arxiv_id):
    model_dict = create_model_dict()
    converter = PdfConverter(model_dict)
    

    blob_client = pdf_container_client.get_blob_client(f"{arxiv_id}.pdf")
    pdf_bytes = blob_client.download_blob().readall()

    result = converter(BytesIO(pdf_bytes))

    # print(result)
    # # Step 1: Extract the references section starting from "## REFERENCES"

    text_convert = result.markdown
    references_section = text_convert.split("## REFERENCES", 1)[-1]

    # Step 2: Extract individual references using regex
    # Matches patterns like [1] Author, Title, etc.
    references = re.findall(r"\[\d+\].*?(?=(?:\[\d+\]|$))", references_section, re.DOTALL)

    # Step 3: Clean up extra whitespace
    references = [ref.strip().replace("\n", " ") for ref in references]

    # print(references)

    # for ref in references:
    #     print(ref)


    cleaned_refs = parse_references_list(references)
    merged_output = merge_simple(cleaned_refs, output)

    # print(f"{merged_output}:")

    return merged_output,arxiv_id

def llm_filter_references(references):

    user_prompt = (
        f"The references of a paper will be passed in. Filter the citations so only the citations that relevant to autonomous vehicles is kept"
        "Return guidance:\n"
        "Return the references in Valid Json"
        f"{references}"
    )
    resp = client.chat.completions.create(
        model=deployment,
        temperature=0.0,
        response_format={"type" :"json_object"},
        messages=[{"role":"user","content":user_prompt}]
    )
    raw = resp.choices[0].message.content.strip()

    # print(raw)
    print(type(raw))
    papers = json.loads(raw)
    return papers
import json

def add_references(raw,arxiv_id):
    # print(raw)

    for paper in raw["references"]:
        # authors_list=[]
        # for author in paper['authors']:
        #     authors_list.append(author)

        summary=driver.execute_query(
        """ 
        MATCH (p:Paper {ArxivId : $arxivId})
        RETURN p.PaperId AS paperId

        """,
        arxivId = arxiv_id,
        database_="paperconnection",
        )
        sepaperid = summary.records[0]["paperId"]

        summary = driver.execute_query("""
        // ensure/prepare the target paper node
        MERGE (a:Paper {PaperId: $targetId, Title: $title, PublicationDate: $publicationDate, Authors : $authors})
        ON CREATE SET a.Title = $title
        // pass a forward
        WITH a
        // find the citing/source paper
        MATCH (b:Paper {PaperId: $sourceId})
        // create the REFERENCES relation
        MERGE (b)-[:REFERENCES]->(a)
        """,
        targetId=paper["paperId"],          # the paper in 'row' (referenced paper)
        title = paper["title"],
        sourceId=sepaperid, 
        authors = paper['authors'], # <-- set this to the citing paper's id
        publicationDate = paper['publicationDate'],
        database_="paperconnection",
    ).summary
        
    print("Created {relationships_created} new references".format(
        relationships_created=summary.counters.relationships_created))

    return arxiv_id

import re

def remove_from_references(md_text: str) -> str:
    # Match a line like: ## REFERENCES, ## **REFERENCES**, or with optional <span> tags
    pattern = r'(?mi)^\s{0,3}##\s*(?:<[^>]*>\s*)*(?:\*\*)?\s*references\s*(?:\*\*)?\s*$'
    m = re.search(pattern, md_text)
    return md_text[:m.start()] if m else md_text

def ask_llm(arxiv_id,md_container_client):
    file = md_container_client.download_blob(f"{arxiv_id}.md")
    md = file.content_as_text()
    md = remove_from_references(md)



    user_prompt = (
        f"An markdown file is passed with abstract/introduction/conclusion/summary"
        "Extract the hypothesis, if there is no hypothesis, then extract the research question from markdown file"
        "Extract the results from the file"
        "Decide if the hypothesis has been met or research question has been met"
        "Return Why has the hypothesis/research question been met or not\n"
        "Return in Valid JSON"
        "Extracted Hypothesis"
        "Extracted Research Question"
        "Hypothesis_Met (True/False)" 
        "Research_Question_Met (True/False)" 
        f"{md}"
    )
    resp = client.chat.completions.create(
        model=deployment,
        temperature=0.0,
        response_format={"type" :"json_object"},
        messages=[{"role":"user","content":user_prompt}]
    )
    raw = resp.choices[0].message.content.strip()
    print(raw)

    return raw,arxiv_id

def upload_hypotheis_and_reasoning(raw,arxiv_id):
    print(arxiv_id)

    # summary=driver.execute_query(
    #     """ 
    #     MATCH (p:Paper {ArxivId : $arxivId})
    #     RETURN p.PaperId AS paperId

    #     """,
    #     arxivId = arxiv_id,
    #     database_="paperconnection",
    #     )
    # sepaperid = summary.records[0]["paperId"]

    details = json.loads(raw)


#     driver.execute_query(
#     """
#     MATCH (p:Paper {ArxivId: $arxivId})
#     SET p.Extracted_Hypothesis = $extracted_hypothesis,
#         p.Extracted_Research_Question = $extracted_research_question,
#         p.Hypothesis_Met = $hypothesis_met,
#         p.Research_Question_Met = $research_question_met,
#         p.Reasoning = $reasoning
#     RETURN 
#         p.PaperId AS PaperId,
#         p.Extracted_Hypothesis AS Extracted_Hypothesis,
#         p.Extracted_Research_Question AS Extracted_Research_Question,        
#         p.Hypothesis_Met AS Hypothesis_Met,
#         p.Research_Question_Met AS Research_Question_Met,
#         p.Reasoning AS Reasoning
#     """,
#     arxivId=arxiv_id,
#     extracted_hypothesis=details["Extracted_Hypothesis"],
#     extracted_research_question=details["Extracted_Research_Question"],
#     hypothesis_met=details["Hypothesis_Met"],
#     research_question_met=details["Research_Question_Met"],
#     reasoning=details["Reasoning"],
#     database_="paperconnection",
# )

    driver.execute_query(
        """
        MATCH (p:Paper {ArxivId: $arxivId})

        // If we have a hypothesis, attach it and set its 'Met' (and optional reasoning)
        FOREACH (_ IN CASE WHEN $extracted_hypothesis IS NULL THEN [] ELSE [1] END |
        MERGE (h:Hypothesis {paperArxivId: $arxivId, text: $extracted_hypothesis})
        MERGE (p)-[:HAS_HYPOTHESIS]->(h)
        SET h.Met = $hypothesis_met,
            h.Reasoning = $reasoning
        )

        // If we have a research question, attach it and set its 'Met' (and optional reasoning)
        FOREACH (_ IN CASE WHEN $extracted_research_question IS NULL THEN [] ELSE [1] END |
        MERGE (rq:ResearchQuestion {paperArxivId: $arxivId, text: $extracted_research_question})
        MERGE (p)-[:HAS_RESEARCH_QUESTION]->(rq)
        SET rq.Met = $research_question_met,
            rq.Reasoning = $reasoning
        )

        """,
        arxivId=arxiv_id,
        extracted_hypothesis=details["Extracted_Hypothesis"],
        extracted_research_question=details["Extracted_Research_Question"],
        hypothesis_met=details["Hypothesis_Met"],
        research_question_met=details["Research_Question_Met"],
        reasoning=details["Reasoning"],
        database_="paperconnection",
    )




    return

    


if __name__ == "__main__":
    get_arxiv_papers(arxiv_ids,pdf_container_client)

    # print(f"extracted arxiv ids:{arxiv_ids}")

    convert_pdf_to_markdown_from_azure()
    query_semantic_api(arxiv_ids)

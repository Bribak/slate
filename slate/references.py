"""The paper behind a PMID or DOI, for the editor's hover and the names in the folder tree and tabs (app.js, references):
title, authors, journal and year, from PubMed's esummary and doi.org's citation data (CSL JSON, which answers for Crossref
and DataCite DOIs alike).

Only the identifier is sent, and only when one is hovered; nothing runs otherwise. Answers are kept in references.json
next to state.json, the newest KEPT, so a paper is asked for once. A refusal or no network is not kept: the next hover
asks again. Off with "reference_lookup": false.
"""
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from slate import state

PUBMED = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id='
DOI_ORG = 'https://doi.org/'
AGENT = 'slate (https://github.com/Bribak/slate)'
PMID = re.compile(r'\d{1,9}')
DOI = re.compile(r'10\.\d{4,9}/\S+')
TAGS = re.compile(r'<[^>]+>')  # doi.org's titles carry <i> and <sub>
KEPT = 2000
TIMEOUT = 6
_lock = threading.Lock()
_known = None  # {key: record or {'missing': True}}, oldest first


def cache_file():
    return state.STATE_FILE.parent / 'references.json'


def fetch(url, accept = 'application/json'):
    request = urllib.request.Request(url, headers = {'User-Agent': AGENT, 'Accept': accept})
    with urllib.request.urlopen(request, timeout = TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8'))


def pubmed(pmid):
    doc = fetch(PUBMED + pmid).get('result', {}).get(pmid)
    if not doc or 'error' in doc or not doc.get('title'):
        return None
    doi = next((a['value'] for a in doc.get('articleids', []) if a.get('idtype') == 'doi'), None)
    return {'title': doc['title'].rstrip('.'), 'authors': [a['name'] for a in doc.get('authors', []) if a.get('authtype', 'Author') == 'Author'],
            'journal': doc.get('fulljournalname') or doc.get('source'), 'year': (doc.get('pubdate') or '')[:4], 'pmid': pmid, 'doi': doi}


def doi_org(doi):
    try:
        doc = fetch(DOI_ORG + urllib.parse.quote(doi, safe = '/'), accept = 'application/vnd.citationstyles.csl+json')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    title = doc.get('title') or ''
    title = TAGS.sub('', title[0] if isinstance(title, list) and title else title if isinstance(title, str) else '')
    journal = doc.get('container-title') or doc.get('publisher')
    parts = ((doc.get('issued') or doc.get('published') or {}).get('date-parts') or [[None]])[0]
    authors = [' '.join(filter(None, (a.get('given'), a.get('family')))) or a.get('literal', '') for a in doc.get('author', [])]
    return {'title': title.strip().rstrip('.'), 'authors': [a for a in authors if a], 'journal': journal[0] if isinstance(journal, list) and journal else journal,
            'year': str(parts[0]) if parts and parts[0] else '', 'pmid': doc.get('PMID'), 'doi': doc.get('DOI') or doi}


def lookup(kind, ident):
    """{title, authors, journal, year, pmid, doi}, {'missing': True} when PubMed or doi.org has no such record, or None when
    neither could be reached."""
    global _known
    ident = ident.strip()
    if kind == 'pmid' and PMID.fullmatch(ident):
        key, ask = 'pmid:' + str(int(ident)), lambda: pubmed(str(int(ident)))
    elif kind == 'doi' and DOI.fullmatch(ident):
        key, ask = 'doi:' + ident.lower(), lambda: doi_org(ident)
    else:
        return {'missing': True}
    with _lock:
        if _known is None:
            try:
                _known = json.loads(cache_file().read_text(encoding = 'utf-8'))
            except (OSError, ValueError):
                _known = {}
        if key in _known:
            return _known[key]
    try:
        record = ask() or {'missing': True}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):  # offline or refused (URLError, HTTPError and timeouts are OSErrors), or a reply of another shape
        return None
    with _lock:
        _known[key] = record
        for old in list(_known)[:max(0, len(_known) - KEPT)]:
            del _known[old]
        try:
            cache_file().parent.mkdir(parents = True, exist_ok = True)
            cache_file().write_text(json.dumps(_known), encoding = 'utf-8')
        except OSError:
            pass
    return record


def link(url):
    """True for the pages a reference hover opens: a PubMed record or a DOI."""
    return bool(re.fullmatch(r'https://pubmed\.ncbi\.nlm\.nih\.gov/\d{1,9}/|https://doi\.org/10\.\d{4,9}/\S+', url))

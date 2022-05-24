import os
import requests
import datetime
import json

from STSTypes import *

from collections import namedtuple
from bs4 import BeautifulSoup as soup
from strsimpy.jaro_winkler import JaroWinkler
from urllib3.exceptions import InsecureRequestWarning

from flask import Flask, request


WikiEntries = {}
EntryByName = {}

cache_update = None
force_update = False

def create_entry_key(name, entry_type):
    return ';'.join([name, entry_type])

def create_entry_key(entry: WikiEntry):
    return ';'.join([entry.name(), entry.entry_type()])


def log(text):
    """helper to log to file and print at the same time"""
    log_text = text.replace('\n', '\n\t')
    print(text)

class STSWikiReader:
    """Reads data from website, creates a lookup map of item names, and does
        soft string matching to find possible mentions of the item parsed
    """
    strcmp = JaroWinkler()

    def __init__(self, name, reader_type, links, ignore_list, parse_names, gen_desc):
        self.last_update = cache_update if cache_update and not force_update else datetime.datetime.utcnow()
        self.name = name
        if reader_type not in EntryTypes:
            raise Exception(f'reader_type {reader_type} is not a supported type. It should be one of the following:\n' + \
                  ', '.join(EntryTypes));
        
        self.reader_type = reader_type
        self.links = links
        
        self.ignore_list = ignore_list
        self.parse_names = parse_names
        self.gen_desc = gen_desc

        self.base_set = set()
        
        if not cache_update or force_update or(datetime.datetime.utcnow() - self.last_update).days > 15:
            self.update_info()
        else:
            for key in WikiEntries:
                entry = WikiEntries[key]
                if entry.entry_type() == reader_type:
                    self.base_set.add(entry.name())

    def update_info(self):
        """goes to the web and finds information provided by the links"""
        log(f'Updating {self.name}s...')
        seen_list = set()

        # fetch data from links and update object with most recent info
        for link in self.links:
            res = requests.get(link, verify=False)
            res_soup = soup(res.text, features="html.parser")
            _class = res_soup.find(id='firstHeading').text.replace('Cards', '').strip()
            for cur_name, data in self.parse_names(res_soup):
                if cur_name.lower() in self.ignore_list:
                    continue
                data['class'] = _class
                seen_list.add(cur_name)
                # if we haven't seen it before, add it to our look up list.
                if not cur_name.startswith('Category:'):
                    entry = WikiEntry(cur_name, self.reader_type, '', data['link'])
                    entry['descr'] = self.gen_desc(entry, data)
                    
                    WikiEntries[create_entry_key(entry)] = entry
                    EntryByName[cur_name.lower()] = entry
                    self.base_set.add(cur_name)

        #remove missing entries
        missing = self.base_set - seen_list
        if missing:
            for item in missing:
                del WikiEntries[create_entry_key(item, self.reader_type)]
                del EntryByName[item]
                self.base_set.remove(item)

        # finalize update
        self.last_update = datetime.datetime.utcnow()
        log(f'Found {len(seen_list)} {self.name}s')

def select_single(page, txt):
    chunk = page.select(txt)
    if chunk:
        chunk = chunk[0]
    chunk = chunk.text.strip()
    chunk = chunk[chunk.find('\n'):].strip()
    return chunk
    

def build_relic_desc(entry, data):
    res = requests.get(entry.link(), verify=False)
    page = soup(res.text, features='html.parser')
    
    desc   = select_single(page, 'div[data-source="description"]')
    rarity = select_single(page, 'div[data-source="rarity"]')
    _class = select_single(page, 'div[data-source="class"]')

    chunk = f'* [{entry.name()}]({entry.link()}) {rarity} '
    if _class.lower() != 'any':
        chunk += f'({_class} only) '
    chunk += f'Relic\n\n {desc}'
    return chunk

def build_card_desc(entry, data):
    # the names are wrong for curses due to table position differences.
    # Rather than do this right (each card type has its positions/orders within a class struct)
    # I'm doing it quick (Use the wrong positions/names to build the correct output)
    def build_curse_desc():
        res = f'* [{entry.name()}]({entry.link()})'
        if 'class' in data:
            res += f' {data["class"]}'
        res += '\n\n    '
        if 'energy' in data:
            res += f' {data["energy"]}'
        if 'rarity' in data:
            res += f' {data["rarity"]}'
        if 'type' in data:
            res += f' {data["type"]}'
        if 'effect' in data:
            res += f' {data["effect"]}'
        return res

    res = ''
    if 'class' in data and data['class'].lower() in ['curse', 'status']:
        return build_curse_desc()
            
    res = f'* [{entry.name()}]({entry.link()})'
    if 'class' in data:
        res += f' {data["class"]}'
    if 'rarity' in data:
        res += f' {data["rarity"]}'
    if 'type' in data:
        res += f' {data["type"]}'
    res += '\n\n    '
    if 'energy' in data:
        res += f' {data["energy"]} Energy |'
    if 'effect' in data:
        res += f' {data["effect"]}'
    return res

def update_entries():
    for key in WikiEntries:
        entry = WikiEntries[key]
        if entry.entry_type() != EntryCardType:
            continue
        res = requests.get(entry.link, verify=False)
        page = soup(res.text, features='html.parser')
        desc = ''
        if entry.entry_type() == EntryRelicType:
            desc = build_relic_desc(page)
        elif entry.entry_type() == EntryCardType:
            desc = build_card_desc(page)
        entry['descr'] = desc

def load_cache(name):
    global force_update
    global cache_update
    global WikiEntries
    global EntryByName
    data = {}
    if os.path.exists(name):
        with open(name) as stream:
            data = json.load(stream)
            force_update = data['force_update']
            cache_update = datetime.datetime.fromisoformat(data['cache_update'])
            entries  = data['WikiEntries']
            for key in entries:
                entry = WikiEntry(**entries[key])
                WikiEntries[key] = entry
                EntryByName[entry.name().lower()] = entry
    

def save_cache(name):
    data = {'force_update': force_update, 'cache_update': (cache_update or datetime.datetime.utcnow()).isoformat(), 'WikiEntries': WikiEntries}
    with open(name, 'w') as stream:
        json.dump(data, stream)

def try_save_cache():
    def do_save():
        cache_update = max(CardReader.last_update, RelicReader.last_update)
        force_update = False
        save_cache(cache_name)
        
    global readers
    update = force_update
    if update:
        do_save()
    else:
        for reader in readers:
            update = update or reader.last_update != cache_update;
            if update:
                do_save()
                return None

readers = []        
app = Flask(__name__)

req = None

@app.route('/describe', methods=['POST'])
def describe_many():
    global EntryByName
    global req
    result = []
    req = request
    names = [s.lower() for s in request.json['names']]
    for name in names:
        if name in EntryByName:
            result.append(EntryByName[name])
    return {'entries': result}

@app.route('/describe/<name>')
def describe(name):
    global EntryByName
    result = None
    name = name.lower()
    if name in EntryByName:
        result = EntryByName[name]
    return {'entries': result}

@app.route('/update')
def update():
    global readers
    force_update = True
    for reader in readers:
        reader.update_info()
    try_save_cache()
    return 'Updated'

@app.route('/', methods=['GET'])
def index():
    res = 'Server is live! We have'
    is_first = True
    for reader in readers:
        cnt = len(reader.base_set)
        r_type = reader.reader_type
        if cnt != 1:
            r_type += 's'
        if not is_first:
            res += ','
        else:
            is_first = False
        res += f' {cnt} {r_type}'
    return res

cache_name = 'sts_descr.cache'
    
if __name__ == '__main__':
    def relic_parse(page_soup):
        return [(a.text, {'link': "https://slay-the-spire.fandom.com"+ a['href']}) for a in page_soup.find_all(
            class_='category-page__member-link')]

    def clean_text(data):
        res = None
        if data and data.text:
            res = data.text.strip()
        return res
        
    def card_parse(page_soup):
        data = [row for row in page_soup.find('table').find_all('tr')]
        res = []
        
        for d in data:
            rarity = ''
            _type = ''
            energy = ''
            effect = ''
            a = d.find_next('a')
            cnt = 0
            if d:
                for el in d.find_all('td'):
                    val = clean_text(el)
                    if not val:
                        continue
                    cnt = cnt + 1
                    if cnt == 2:
                        rarity = val
                    elif cnt == 3:
                        _type = val
                    elif cnt == 4:
                        energy = val
                    elif cnt == 5:
                        effect = val
            if effect:
                effect = effect.strip()
                
            res.append((a.text, {
                'link': "https://slay-the-spire.fandom.com"+ a['href'],
                'rarity': rarity,
                'type': _type,
                'energy': energy,
                'effect': effect}))
        return res
    def get_data(filename):
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                return [k.strip() for k in f.readlines()]

    # Read from files
    checked_ids = get_data('checked.txt')
    RELIC_IGNORE = [i.lower() for i in get_data('relic.ignore')]
    RELIC_LINKS = get_data('relic.link')
    CARD_IGNORE = [i.lower() for i in get_data('card.ignore')]
    CARD_LINKS = get_data('card.link')

    # Setup
    requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
    load_cache(cache_name)
    CardReader = STSWikiReader('card',
                               EntryCardType,
                               CARD_LINKS,
                               CARD_IGNORE,
                               card_parse,
                               build_card_desc)
    RelicReader = STSWikiReader('relic',
                                EntryRelicType,
                                RELIC_LINKS,
                                RELIC_IGNORE,
                                relic_parse,
                                build_relic_desc)

    readers = [CardReader, RelicReader]

    try_save_cache()
    
    app.run()
    #update_entries()
    #RelicReader.update_info

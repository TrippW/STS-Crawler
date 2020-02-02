import requests
import praw
import prawcore
import datetime
import os
from urllib3.exceptions import InsecureRequestWarning
from strsimpy.jaro_winkler import JaroWinkler


IGNORE = ['Relics']

class STSRelicReader:
    def __init__(self, relics=None, real_relic_names=None, fake_relic_name_map=None):
        self.last_relic_update = datetime.datetime.utcnow()
        self.relics = set()
        self.real_relic_names = set()
        self.fake_relic_name_map = dict()
        if relics:
            self.relics = relics
        if real_relic_names:
            self.real_relic_names = real_relic_names
        if fake_relic_name_map:
            self.fake_relic_name_map = fake_relic_name_map
        self.cur_relic = None
        self.max_match = 0
        self.max_relic_name_words = 0
        self.strcmp = JaroWinkler()

        self.update_relic_set()

    def _get_relic_name(self, string):
        title_start = string.index('title=')+7
        title_end = string.index('"', title_start)
        if string.count('(', title_start) > 0:
            title_end = min(string.index('(', title_start)-1, title_end)
        return string[title_start:title_end]

    def format_relic_name(self, name):
        return self._rm_symbol(self._rm_squote(self._rm_hyph(name.lower())))

    def _rm_symbol(self, name):
        return name.replace('?', '').replace(',', '').replace('.', '').replace('!', '')

    def _rm_squote(self, name):
        return name.replace("'", '')

    def _lower(self, name):
        return name.lower()

    def _rm_hyph(self, name):
        return name.replace('-',' ').replace('_',' ')

    def _rm_beta(self, name):
        return name.replace('_beta', '').replace('_Beta', '').replace('Beta', '').replace('beta', '')

    def _gen_alternative_names(self, name):
        names = set([])
        actions = [self._rm_symbol, self._rm_squote, self._lower, self._rm_hyph, self._rm_beta]
        for outer in range(len(actions)):
            temp_name = name
            for inner in range(len(actions)-outer):
                temp_name = actions[outer+inner](temp_name)
                names.add(temp_name)
        return list(names)

    def update_relic_set(self):
        print('Updating relics...')
        links = [
            'https://slay-the-spire.fandom.com/wiki/Category:Relic',
            'https://slay-the-spire.fandom.com/wiki/Category:Beta_Relic'
        ]
        global IGNORE
        for link in links:
            res = requests.get(link, verify=False)
            for t in res.text.split('\n'):
                if 'category-page__member-link' in t:
                    cur_name = self._get_relic_name(t)
                    if ((not cur_name in self.relics)
                        and (not cur_name in IGNORE)
                        and (not cur_name.startswith('Category:'))):
                        self.relics.add(cur_name)
                        self.real_relic_names.add(cur_name)
                        self.fake_relic_name_map[cur_name] = cur_name
                        self.max_relic_name_words = max(self.max_relic_name_words, len(cur_name.split(' ')))
                        
                        for new_name in self._gen_alternative_names(cur_name):
                            self.relics.add(new_name)
                            self.fake_relic_name_map[new_name] = cur_name
            self.last_relic_update = datetime.datetime.utcnow()
        print('Found {} relics'.format(len(self.real_relic_names)))



    def get_real_relic_name(self, name):
        return self.fake_relic_name_map[name]

    def clean_name(self, name):
        name = name.lower()
        

    def check_if_similar(self, name):
        name = self.format_relic_name(name)
        split_name = name.split(' ')
        word_thresh = 0.9**len(split_name)
        self.max_match = 0
        self.cur_relic = None
        for rel_name in self.relics:
            split_rel_name  = rel_name.split(' ')
            if len(split_name) == len(split_rel_name):
                word_check = 1
                for i in range(len(split_name)):
                    word_check *= self.strcmp.similarity(split_name[i], split_rel_name[i])
                    word_check *= self.strcmp.similarity(split_name[i][::-1], split_rel_name[i][::-1])

                if word_check > self.max_match:
                    self.max_match = word_check
                    if word_check >= word_thresh:
                        self.cur_relic = self.get_real_relic_name(rel_name)
        return self.cur_relic != None

    def check_if_relic(self, name, update=True):
        if update and datetime.datetime.utcnow() - self.last_relic_update > datetime.timedelta(days=15):
            self.update_relic_set()
            
        res = name in self.real_relic_names
        if res:
            self.cur_relic = name
            self.max_match = 1
        elif name in self.relics:
            self.cur_relic = self.get_real_relic_name(name)
            self.max_match = 1
        else:
            res = self.check_if_similar(name)
        return res

class RedditBot:
    def __init__(self, relicReader):
        self.REDDIT = self.login()
        self.SUBREDDIT = self.REDDIT.subreddit('slaythespire')
        self.relicReader = relicReader
        self.NEW_LINE = '\n\n'
        self.REPLY_TEMPLATE = 'I am {:0.1f}% confident you mentioned [[{}]] in your post.'
                              
        self.END_TEXT = 'Let me call the bot for you.\n\n' + '-'*50 + \
                        self.NEW_LINE +'I am a bot response, but I am using my creators account. ' + \
                        'Please reply to me if I got something wrong so he can fix it.'

    def login(self):
        """
        log in to reddit
        uses a praw.ini file to hold sensitive information
        """

        return praw.Reddit(redirect_uri='http://localhost:8080', \
                           user_agent='STS Scraper by /u/devTripp')

    def start(self):
        global can_post
        self.posted = False
        while True:
            print('Starting up...')
            try:
                for post in self.SUBREDDIT.stream.submissions():
                    self.process_submission(post)
            except Exception as e:
                print(e)
                #raise e

    def check_all_word_combos(self, title, on_true):
        if datetime.datetime.utcnow() - self.relicReader.last_relic_update > datetime.timedelta(days=15):
            self.relicReader.update_relic_set()
        words = title.split(' ')
        mentions = dict()
        found = False
        for word_pos in range(len(words)):
            for offset in range(1, self.relicReader.max_relic_name_words+1):
                if word_pos + offset > len(words):
                    break
                phrase = ' '.join(words[word_pos:word_pos+offset])
                if self.relicReader.check_if_relic(phrase, False):
                    if not found:
                        print(title)
                    cur_relic = self.relicReader.cur_relic
                    print('Relic Mention: {} | {:0.2f}'.format(cur_relic, self.relicReader.max_match))
                    if cur_relic in mentions.keys():
                        mentions[cur_relic] = max(self.relicReader.max_match*100, mentions[cur_relic])
                    else:
                        mentions[cur_relic] = self.relicReader.max_match*100
                    found = True

        if found:
            on_true(mentions)

        return found

    def post_reply(self, items):
        reply = ""
        for key in items:
            reply += self.REPLY_TEMPLATE.format(items[key], key) + self.NEW_LINE
        reply += self.END_TEXT
        
        print(reply)
        self.post.reply(reply)

    def process_submission(self, post):
        global checked_ids
        title = post.title
        self.post = post

        title = str(title).encode('utf-8', errors='ignore').decode('utf-8')
        if (post.id not in checked_ids) and 'Daily Discussion' not in title:
            print('checking {}'.format(post.id))
            self.check_all_word_combos(title, self.post_reply)
            checked_ids.append(post.id)
            with open('checked.txt', 'a') as f:
                f.write('\n'+post.id)

            
            
checked_ids = []
can_post = True

if __name__=='__main__':
    if os.path.exists('checked.txt'):
        with open('checked.txt', 'r') as f:
            checked_ids = [k.replace('\n','') for k in f.readlines()]
    
    requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
    RelicReader = STSRelicReader()
    redditbot = RedditBot(RelicReader)
    redditbot.start()
    
"""document.getElementsByClassName("category-page__member-link").forEach((a)=>console.log(a.text))"""

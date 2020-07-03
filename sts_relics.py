"""This unit handles reading in data from the slayTheSpire wiki,
    creating a string comparison tool,
    reading posts from the slay the spire subreddit
    scanning the post titles for item mentions and replying with data
"""
import requests
import praw
import prawcore
import datetime
import os
from urllib3.exceptions import InsecureRequestWarning
from strsimpy.jaro_winkler import JaroWinkler

RELIC_IGNORE = ['Relics']
RELIC_LINKS = [
    'https://slay-the-spire.fandom.com/wiki/Category:Relic',
    'https://slay-the-spire.fandom.com/wiki/Category:Beta_Relic'
]

def log(text):
    """helper to log to file and print at the same time"""
    with open('sts_crawler.log', 'a') as logger:
        log_text = text.replace('\n', '\n\t')
        logger.write(f'\n{str(datetime.datetime.utcnow())}: {log_text}')
    print(text)

class STSWikiReader:
    """Reads data from website, creates a lookup map of item names, and does
        soft string matching to find possible mentions of the item parsed
    """
    base_set = set()
    real_names = set()
    fake_name_map = dict()
    cur = None
    max_name_word_cnt = 0

    #For matching strings to items
    max_match = 0
    strcmp = JaroWinkler()

    def __init__(self, name, links, tag_match, ignore_list, parse):
        self.last_update = datetime.datetime.utcnow()
        self.name = name
        self.links = links
        self.tag_match = tag_match
        self.ignore_list = ignore_list
        self.parse = parse

        self.update_info()

    def format_name(self, name):
        """Used to get a clean, uniform name with pesky characters removed"""
        return self._rm_symbol(self._rm_squote(self._rm_hyph(name.lower())))

    def _rm_symbol(self, name):
        """removes odd characters that should never be in a obj name"""
        return name.replace('?', '').replace(',', '').replace('.', '').replace('!', '').replace('(', '').replace(')', '')

    def _rm_squote(self, name):
        """removes single quotes"""
        return name.replace("'", '').replace('’', '')

    def _lower(self, name):
        """exists to pass along to alternative names func"""
        return name.lower()

    def _rm_hyph(self, name):
        """swaps typical joining characters with spaces"""
        return name.replace('-',' ').replace('_',' ')

    def _rm_beta(self, name):
        """removes beta tag (possible error from wiki)"""
        return name.replace('_beta', '').replace('_Beta', '').replace('Beta', '').replace('beta', '')

    def _gen_alternative_names(self, name):
        """creates a massive list of possible mistypes for a specific name, used as an aid for matching user input"""
        names = set()
        actions = [self._rm_symbol, self._rm_squote, self._lower, self._rm_hyph, self._rm_beta]
        for outer in range(len(actions)):
            temp_name = name
            for inner in range(len(actions)-outer):
                temp_name = actions[outer+inner](temp_name)
                names.add(temp_name)
        return list(names)

    def update_info(self):
        """goes to the web and finds information provided by the links"""
        log(f'Updating {self.name}s...')
        seen_list = set()

        #fetch data from links and update object with most recent info
        for link in self.links:
            res = requests.get(link, verify=False)
            for t in res.text.split('\n'):
                if self.tag_match in t:
                    cur_name = self.parse(t)
                    seen_list.add(cur_name)
                    #if we haven't seen it before, add it to our look up list.
                    if ((not cur_name in self.base_set)
                        and (not cur_name in self.ignore_list)
                        and (not cur_name.startswith('Category:'))):
                        self.base_set.add(cur_name)
                        self.real_names.add(cur_name)
                        self.fake_name_map[cur_name] = cur_name
                        self.max_name_word_cnt = max(self.max_name_word_cnt, len(cur_name.split(' ')))
                        
                        for new_name in self._gen_alternative_names(cur_name):
                            self.base_set.add(new_name)
                            self.fake_name_map[new_name] = cur_name

        #handle deleted data from wiki
        recalc_max_name_word_cnt = False
        for cur_name in self.real_names - seen_list:
            for new_name in self._gen_alternative_names(cur_name):
                self.base_set.remove(new_name)
                del self.fake_name_map[new_name]

            if not recalc_max_name_word_cnt and self.max_name_word_cnt == len(cur_name.split(' ')):
                recalc_max_name_word_cnt = True
            self.base_set.remove(cur_name)
            self.real_names.remove(cur_name)
            del self.fake_name_map[cur_name]

        if recalc_max_name_word_cnt:
            self.max_name_word_cnt = 0
            for cur_name in self.real_names:
                self.max_name_word_cnt = max(self.max_name_word_cnt, len(cur_name.split(' ')))

        #finalize update    
        self.last_update = datetime.datetime.utcnow()
        log('Found {} {}s'.format(len(self.real_names), self.name))

    def check_if_similar(self, name):
        """uses similarity check to see if the passed in name may match any of our found or generated names"""
        name = self.format_name(name)
        split_name = name.split(' ')
        word_thresh = 0.9**len(split_name)
        self.max_match = 0
        self.cur = None
        for item_name in self.base_set:
            split_item_name  = item_name.split(' ')
            if len(split_name) == len(split_item_name):
                word_check = 1
                for i in range(len(split_name)):
                    word_check *= self.strcmp.similarity(split_name[i], split_item_name[i])
                    word_check *= self.strcmp.similarity(split_name[i][::-1], split_item_name[i][::-1])

                if word_check > self.max_match:
                    self.max_match = word_check
                    if word_check >= word_thresh:
                        self.cur = self.fake_name_map[item_name]
        return self.cur != None

    def check_if_exists(self, name, update=True):
        """Used to check if a name is a perfect match for any found names or is close enough to call a match"""
        if update and datetime.datetime.utcnow() - self.last_update > datetime.timedelta(days=15):
            self.update_info()
            
        res = name in self.real_names
        if res:
            self.cur = name
            self.max_match = 1
        elif name in self.fake_name_map.keys():
            self.cur = self.fake_name_map[name]
            self.max_match = 1
            res = True
        else:
            res = self.check_if_similar(name)
        return res

class RedditBot:
    last_update=None
    def __init__(self, readers):
        self.REDDIT = self.login()
        self.SUBREDDIT = self.REDDIT.subreddit('slaythespire')
        self.readers = readers
        self.NEW_LINE = '\n\n'
        self.FIRST_REPLY_TEMPLATE = 'I am {:0.1f}% confident you mentioned {} in your post.'
        self.REPLY_TEMPLATE = 'I am also {:0.1f}% confident you mentioned {}.'
                              
        self.END_TEXT = 'Let me call the bot for you.' + \
                        self.NEW_LINE + '-'*50 + \
                        self.NEW_LINE + "I am a bot response, but I am using my creator's account. " + \
                            'Please reply to me if I got something wrong so he can fix it.' + \
                        self.NEW_LINE + '[Source Code](https://github.com/TrippW/STS-Crawler)'

    def login(self):
        """
        log in to reddit
        uses a praw.ini file to hold sensitive information
        """
        return praw.Reddit(redirect_uri='http://localhost:8080', \
                           user_agent='STS Scraper by /u/devTripp')

    def start(self):
        """starts the bot, runs forever"""
        while True:
            log('Starting up...')
            try:
                if False and datetime.datetime.utcnow() - self.last_update  > datetime.timedelta(days=1):
                    pass#self.update_ignore_files()
                for post in self.SUBREDDIT.stream.submissions():
                    self.process_submission(post)

            except Exception as e:
                log(e)

    def update_ignore_files(self):
        """finds ignore files for our reader and pulls data into the readers. Used to update ignored strings during runtime"""
        for reader in self.readers:
            fname = f'{reader.name}.ignore'
            if os.path.exists(fname):
                with open(fname, 'r') as f:
                    reader.ignore_list = [k.strip() for k in f.readlines()]
            else:
                reader.ignore_list = []

        self.last_update = datetime.datetime.utcnow()

    def check_all_word_combos(self, title, on_true):
        """breaks the sentence/title into words/groups of words, and tries to match it with data in a reader"""

        for reader in self.readers:
            if datetime.datetime.utcnow() - reader.last_update > datetime.timedelta(days=15):
                reader.update_info()
            words = title.split(' ')
            mentions = dict()
            found = False
            for word_pos in range(len(words)):
                for offset in range(1, reader.max_name_word_cnt+1):
                    if word_pos + offset > len(words):
                        break
                    phrase = ' '.join(words[word_pos:word_pos+offset])
                    if reader.check_if_exists(phrase, False):
                        if not found:
                            log(title)
                        cur = reader.cur
                        print('Relic Mention: {} | {:0.2f}'.format(cur, rreader.max_match))
                        if cur in mentions.keys():
                            mentions[cur] = max(reader.max_match*100, mentions[cur])
                        else:
                            mentions[cur] = reader.max_match*100
                        found = True

        if found:
            on_true(mentions)

        return found

    def post_reply(self, items):
        """formats and posts the data to reddit"""                
        reply = ""
        grouped_items = dict()
        #group by percent
        for key in items:
            k = int(items[key]*10)
            if k not in grouped_items.keys():
                grouped_items[k] = [key]
            else:
                grouped_items[k].append(key)
        template = self.FIRST_REPLY_TEMPLATE
        for key in sorted(grouped_items, reverse=True):
            values = grouped_items[key]
            if len(values) == 1:
                reply += template.format(key/10, f'[[{values[0]}]]') + self.NEW_LINE
            else:
                item_list = ', '.join([f'[[{k}]]' for k in values[:-1]])
                #oxford comma
                if len(values) != 2:
                       item_list += ','
                item_list += ' and ' + f'[[{values[-1]}]]'
                reply += template.format(key/10, item_list) + self.NEW_LINE
            template = self.REPLY_TEMPLATE

        log(reply)

        ###DEBUG TEXT###
        global time_at_run
        log(f'last time sts_relics was run: {time_at_run}')
        log(f'last time ignores were updated: {self.last_update}')
        for reader in self.readers:
            log(f'last time {reader.name} reader was updated: {reader.last_update}')
        ###END DEBUG###
        reply += self.END_TEXT
        self.post.reply(reply)

    def process_submission(self, post):
        """handles input of new posts to the subreddit"""
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
time_at_run = datetime.datetime.utcnow()

if __name__=='__main__':
    def relic_parse(string):
        title_start = string.index('title=')+7
        title_end = string.index('"', title_start)
        if string.count('(', title_start) > 0:
            title_end = min(string.index('(', title_start)-1, title_end)
        return string[title_start:title_end]

    def get_data(filename):
        if os.path.exists(filename):
            with open(filename, 'r') as f:
                return [k.strip() for k in f.readlines()]

    #Read from files            
    checked_ids = get_data('checked.txt')
    RELIC_IGNORE = get_data('relic.ignore')

    #Setup and run bot
    requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
    RelicReader = STSWikiReader('relic', RELIC_LINKS, 'category-page__member-link', RELIC_IGNORE, relic_parse)
    redditbot = RedditBot([ RelicReader ])
    redditbot.start()

    ###FOR TESTING#######################
    #
    #class tempPost:
    #    def __init__(self, title, _id):
    #        self.title = title
    #        self.id = _id
    #redditbot.process_submission(tempPost('Mummified Hand, Amplify, Astrolabe: Creative AI)', '1'))

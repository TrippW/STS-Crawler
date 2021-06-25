EntryCardType = 'Card'
EntryPotionType = 'Potion'
EntryRelicType = 'Relic'

EntryTypes = (EntryCardType, EntryPotionType, EntryRelicType)


class WikiEntry(dict):
    def __init__(self, name, entry_type, descr, link):
        dict.__init__(self, name=name, entry_type=entry_type, descr=descr,link=link)

    def name(self):
        return self['name']

    def entry_type(self):
        return self['entry_type']

    def descr(self):
        return self['descr']

    def link(self):
        return self['link']

    def __str__(self):
        return f'WikiEntry(name: {self.name}, type: {self.entry_type}, link: {self.link}, description: \n{self.descr})'

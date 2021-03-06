import argparse
import mechanize
import re
from bs4 import BeautifulSoup, SoupStrainer
import pickle
import time
import urllib2

from utils import util, file_logger


def parse_args():
    '''
    Parses the node2vec arguments.
    '''
    parser = argparse.ArgumentParser(description="Get node embeddings.")

    parser.add_argument('--depth', default=1,
                        help='Crawl depth.')

    parser.add_argument('--author', default='',
                        help='Author\'s name for log file. Default is empty string. (Only used when author-page!=None)')

    parser.add_argument('--author-page', default=None,
                        help='Use to get single ambiguous author. Author\'s disambiguation page. Default is None (use prolific page).')

    parser.add_argument('--output', default='dblp-data.pckl',
                        help='Output file.')

    parser.add_argument('--restart', action='store_true',
                        help='Restart crawl. Don\'t use cached data.')

    parser.add_argument('--log', action='store_true',
                        help='Log stdout and stderr.')

    return parser.parse_args()


authors = set()
coauthors = {}
papers = {}
ambiguous = {}
completed = set()
links_visited = set()
links_to_visit = []


# http://dblp.uni-trier.de/pers/hd/z/Zhang:Shao_Jie
# http://dblp.uni-trier.de/pers/hd/g/Gao_0001:Wen?q=Wen%24+Gao%24+0001%24
# http://dblp.uni-trier.de/pers/hd/p/Poor:H=_Vincent?q=H.%24+Vincent%24+Poor%24
def get_author_id_from_url(url):
    regex = 'http:\/\/dblp\.uni-trier\.de\/pers\/hd\/[\w=]\/([^\?\n]+)(\?q=)?'
    m = re.match(regex, url)
    if not m:
        raise Exception("Error with url %s being parsed as author" % url)

    return m.groups()[0]


def parse_author_page(br, page, name, depth):
    global coauthors, authors, papers, links_to_visit
    author_id = get_author_id_from_url(page.geturl())
    if author_id in authors:
        return author_id
    coauthors[author_id] = set()
    papers[author_id] = []

    print '\tGetting author: %s (Depth: %d)' % (author_id, depth)

    strainer = SoupStrainer('ul', attrs={'class': 'publ-list'})
    soup = BeautifulSoup(page, 'lxml', parse_only=strainer)

    for article in soup.find_all('li', {'itemtype': 'http://schema.org/ScholarlyArticle'}):
        paper_coauthors = []
        for tag in article.find_all('span', {'itemprop': 'author'}):
            if tag.a:  # If author is not self.
                coauthor_url = tag.a['href']
                coauthor_id = get_author_id_from_url(coauthor_url)

                coauthors[author_id].add(coauthor_id)
                paper_coauthors += [coauthor_id]

        papers[author_id] += [paper_coauthors]

    done = set()
    if depth > 1:
        links = [x for x in br.links(url_regex="pers\/hd\/")]

        for i, link in enumerate(links):
            coauthor_id = link.url.split('/')[-1]

            if coauthor_id in coauthors[author_id] and coauthor_id not in done and coauthor_id not in authors:
                links_to_visit += [(link, depth - 1)]
                done.add(coauthor_id)

    # print '\t\tProcessed %d new coauthors.' % (len(done))

    authors.add(author_id)
    return author_id


def parse_disambiguation_page(br, name, depth):
    global ambiguous, links_to_visit
    print '\tDisambiguating: %s' % (name)
    ambiguous[name] = []
    ambiguous_links = []

    for link in br.links(url_regex="pers\/hd\/"):
        author_id = get_author_id_from_url(link.url)
        ambiguous_links += [(link, depth)]
        ambiguous[name] += [author_id]

    links_to_visit = [links_to_visit[0]] + ambiguous_links + links_to_visit[1:]

    return None


def crawl_page(br, link, depth):
    page = br.follow_link(link)

    page_title = br.title()
    name = link.text

    regex = 'dblp: Author search for '
    if re.match(regex, page_title):
        result = parse_disambiguation_page(br, name, depth)
    else:
        result = parse_author_page(br, page, name, depth)

    br.back()
    return result


def get_initial_links(depth, author_page=None):
    global links_to_visit, links_visited

    if len(links_to_visit) or len(links_visited):
        return

    if not author_page:
        get_prolific_links(depth)
    else:
        print 'Starting crawl. Using author disambiguation page.'
        author_link = mechanize._html.Link('la', author_page, 'Base Author', 'la', 'la')
        links_to_visit = [(author_link, depth)]


def get_prolific_links(depth):
    global links_to_visit
    print 'Starting crawl. Getting all links for prolific pages'

    prolific_page = 'http://dblp.uni-trier.de/statistics/prolific%d'
    for index in range(1, 12):
        br = mechanize.Browser()
        br.open(prolific_page % index)

        links_to_visit += [(x, depth) for x in br.links(url_regex="search\/author\?q\=")]


def crawl_links(depth, author_page=None):
    global completed, links_to_visit, links_visited

    print '\n-----------------------'
    print 'Starting crawl'
    print '-----------------------\n'

    get_initial_links(depth, author_page)

    start_page = 'http://dblp.uni-trier.de/'

    br = mechanize.Browser()
    br.open(start_page)

    util.start_print_progress(5, 'prolific links crawled', initial_count=len(links_visited))

    while links_to_visit:
        util.print_progress(len(links_to_visit) + len(links_visited))

        link, depth = links_to_visit[0]
        if link.url in links_visited:
            links_to_visit.pop(0)
            # print '\t\t\t', link.url, link.text
            continue

        crawl_page(br, link, depth)

        completed.add(link.text)  # Remove?
        links_visited.add(link.url)
        links_to_visit.pop(0)


def save_data(args):
    global coauthors, papers, authors, ambiguous, completed, links_to_visit, links_visited
    data = (coauthors, papers, authors, ambiguous, completed, links_to_visit, links_visited)
    with open(args.output, 'wb') as output:
        pickle.dump(data, output)


def load_data(args):
    global coauthors, papers, authors, ambiguous, completed, links_to_visit, links_visited

    try:
        with open(args.output, 'rb') as input:
            data = pickle.load(input)
        (coauthors, papers, authors, ambiguous, completed, links_to_visit, links_visited) = data

        # print 'Loaded progress. Already completed:', completed
        print 'Loaded progress.'
    except IOError:
        pass


def run_crawler(args):
    crawl = True
    while(crawl):
        crawl = False
        try:
            crawl_links(int(args.depth), args.author_page)
        except urllib2.HTTPError, err:
            if err.code == 429:
                crawl = True
                time.sleep(5 * 60)
            else:
                raise err


def main(args):
    if args.log:
        if args.author_page:
            file_logger.init('%s__crawler__%s' % (args.author, str(time.time())))
        else:
            file_logger.init('crawler__%s' % (str(time.time())))

    if not args.restart:
        load_data(args)

    try:
        run_crawler(args)
        print 'Completed without errors!!!\n'
    except Exception as e:
        print 'Error ocurred! Saving temp data'
        print e
    except KeyboardInterrupt:
        print '\n\nKeyboard interrupted. Saving progress.'

    save_data(args)

    if args.log:
        file_logger.close()


if __name__ == '__main__':
    args = parse_args()
    main(args)

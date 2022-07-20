from tenacity import *
from duckduckgo_search import ddg
import logging


def bd_search_album(album_name, site):
    """
    gogoduck search
    """
    logger = logging.getLogger(__name__)
    logger.info(f'Gogoduck search for {album_name}'.format(album_name=album_name))

    keywords = 'site:{site} "{album_name}"'.format(site=site,
                                                   album_name=album_name)
    results = ddg(keywords, region='fr-fr', safesearch='Moderate', max_results=4)

    if results:
        for res in results:
            if res['href'].startswith('https://www.bedetheque.com/BD-'):  # bedetheque specific
                return res['href']
    else:
        raise ConnectionError

    return results[0]['href']



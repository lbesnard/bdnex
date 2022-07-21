import argparse
import json
import logging
import logging.config
import os
import tempfile
import urllib.request
from pkg_resources import resource_filename

import logging
import sys

from bdnex.lib.colargulog import ColorizedArgsFormatter

LOGGING_CONF = resource_filename('bdnex', "/conf/logging.conf")


def dump_json(json_path, json_data):
    with open(json_path, "w") as outfile:
        json.dump(json_data, outfile, indent=4,
                  sort_keys=True, ensure_ascii=False)


def load_json(json_path):
    if os.path.exists(json_path):
        with open(json_path) as f:
            return json.load(f)


def yesno(question):
    """Simple Yes/No Function."""
    prompt = f'{question} ? (y/n): '
    ans = input(prompt).strip().lower()
    if ans not in ['y', 'n']:
        print(f'{ans} is invalid, please try again...')
        return yesno(question)
    if ans == 'y':
        return True
    return False


def download_link(url, output_folder=None):
    if output_folder is None:
        output_folder = tempfile.mkdtemp()
    else:
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

    urllib.request.urlretrieve(url, os.path.join(output_folder, os.path.basename(url)))

    return os.path.join(output_folder, os.path.basename(url))


def init_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    console_level = "DEBUG"
    console_handler = logging.StreamHandler(stream=sys.stdout)

    console_handler.setLevel(console_level)

    console_format = "%(asctime)s - %(levelname)-8s - %(name)-5s - %(message)s"
    colored_formatter = ColorizedArgsFormatter(console_format)
    console_handler.setFormatter(colored_formatter)
    root_logger.addHandler(console_handler)


def args():
    """
    Returns the script arguments

        Parameters:

        Returns:
            vargs (obj): input arguments
    """
    parser = argparse.ArgumentParser(description='BD metadata retriever')
    parser.add_argument('-f', '--input-file', dest='input_file', type=str, default=None,
                        help="BD file path",
                        required=False)

    parser.add_argument('-d', '--input-dir', dest='input_dir', type=str, default=None,
                        help="BD dir path to process",
                        required=False)

    parser.add_argument('-i', '--init', dest='init',
                        help="initialise or force bdnex to download sitemaps from bedetheque for album matching",
                        required=False)

    parser.add_argument('-v',
                        '--verbose',
                        default='info',
                        help='Provide logging level. default=info')

    init_logging()

    logging.info('Logging now setup.')

    vargs = parser.parse_args()

    if 'vargs.input_file' in locals():
        if not os.path.exists(vargs.input_file):
           raise ValueError('{path} not a valid path'.format(path=vargs.input_file))

    if 'vargs.input_dir' in locals():
        if not os.path.exists(vargs.input_dir):
            raise ValueError('{path} not a valid path'.format(path=vargs.input_dir))

    return vargs


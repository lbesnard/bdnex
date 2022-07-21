import json
import os
import logging
import urllib.request
import tempfile
import argparse


def dump_json(json_path, json_data):
    with open(json_path, "w") as outfile:
        json.dump(json_data, outfile, indent=4,
                  sort_keys=True, ensure_ascii=False)


def load_json(json_path):
    if os.path.exists(json_path):
        with open(json_path) as f:
            return json.load(f)


def download_link(url):
    tmpdir = tempfile.mkdtemp()
    urllib.request.urlretrieve(url, os.path.join(tmpdir, os.path.basename(url)))
    return os.path.join(tmpdir, os.path.basename(url))


class Logging:

    def __init__(self):
        self.logging_filepath = []
        self.logger = []

    def logging_start(self, logging_filepath):
        """ start logging using logging python library
        output:
           logger - similar to a file handler
        """
        self.logging_filepath = logging_filepath
        if not os.path.exists(os.path.dirname(logging_filepath)):
            os.makedirs(os.path.dirname(logging_filepath))

        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

        # create a file handler
        handler = logging.FileHandler(self.logging_filepath)
        handler.setLevel(logging.INFO)

        # create a logging format
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)

        # add the handlers to the logger
        self.logger.addHandler(handler)
        return self.logger

    def logging_stop(self):
        """ close logging """
        # closes the handlers of the specified logger only
        x = list(self.logger.handlers)
        for i in x:
            self.logger.removeHandler(i)
            i.flush()
            i.close()


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

    parser.add_argument('-i', '--init', dest='input_dir', type=str, default=None,
                        help="initialise bdnex by downloading sitemaps from bedetheque for album matching",
                        required=False)

    vargs = parser.parse_args()

    if 'vargs.input_file' in locals():
        if not os.path.exists(vargs.input_file):
           raise ValueError('{path} not a valid path'.format(path=vargs.incoming_path))

    if 'vargs.input_dir' in locals():
        if not os.path.exists(vargs.input-dir):
            raise ValueError('{path} not a valid path'.format(path=vargs.incoming_path))

    return vargs


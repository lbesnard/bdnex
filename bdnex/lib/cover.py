import cv2
import imutils
import logging


def front_cover_similarity(original, image_to_compare):
    """
    check similarity between images
    inspired from pysource website
    :param original:
    :param image_to_compare:
    :return: percentage of confidence of similarity
    """
    logger = logging.getLogger(__name__)
    logger.info('Checking Cover from archive with online cover')

    original = cv2.imread(original, 0)  # convert to grayscale
    image_to_compare = cv2.imread(image_to_compare, 0)  # convert to grayscale

    # resize the images to make them small in size. A bigger size image may take a significant time
    # more computing power and time
    original = imutils.resize(original, height=600)
    image_to_compare = imutils.resize(image_to_compare, height=600)

    # Check for similarities between the 2 images
    sift = cv2.xfeatures2d.SIFT_create()
    kp_1, desc_1 = sift.detectAndCompute(original, None)
    kp_2, desc_2 = sift.detectAndCompute(image_to_compare, None)

    index_params = dict(algorithm=0, trees=5)
    search_params = dict()
    flann = cv2.FlannBasedMatcher(index_params, search_params)

    matches = flann.knnMatch(desc_1, desc_2, k=2)

    good_points = []
    for m, n in matches:
        if m.distance < 0.6*n.distance:
            good_points.append(m)

    # Define how similar they are
    number_keypoints = 0
    if len(kp_1) <= len(kp_2):
        number_keypoints = len(kp_1)
    else:
        number_keypoints = len(kp_2)

    match_percentage = len(good_points) / number_keypoints * 100
    logger.info('Cover matching percentage: {res}'.format(res=match_percentage))

    return match_percentage
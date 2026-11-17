from .classification import classify


def overview(connection):
    return {'topics':classify(connection)}

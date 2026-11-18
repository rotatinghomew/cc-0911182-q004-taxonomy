from .classification import (classify, contributions, describe_version,
                             unassigned_contents, version_report, versions)


def overview(connection, version_id=None, start=None, end=None):
    if version_id is None:
        return {'topics': classify(connection, start=start, end=end)}
    return version_report(connection, version_id, start, end)


def topic_contributions(connection, version_id, topic_id=None, start=None, end=None):
    return contributions(connection, version_id, topic_id, start, end)


def topic_versions(connection):
    return versions(connection)


def version_info(connection, version_id):
    return describe_version(connection, version_id)


def pending_contents(connection, version_id):
    return unassigned_contents(connection, version_id)


__all__ = ['overview', 'topic_contributions', 'topic_versions', 'version_info',
           'pending_contents']

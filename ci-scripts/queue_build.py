# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
from __future__ import absolute_import

import os
import time
import boto3

account = boto3.client(
    "sts", region_name="us-west-2", endpoint_url="https://sts.us-west-2.amazonaws.com"
).get_caller_identity()["Account"]
bucket_name = "sagemaker-us-west-2-%s" % account

MAX_IN_PROGRESS_BUILDS = 3
INTERVAL_BETWEEN_CONCURRENT_RUNS = 15  # minutes
CLEAN_UP_TICKETS_OLDER_THAN = 8  # hours


def queue_build():
    ticket_number = int(1000 * time.time())
    files = _list_tickets()
    _cleanup_tickets_older_than(files)
    _wait_for_other_builds(files, ticket_number)


def _build_info_from_file(file):
    filename = file.key.split("/")[2]
    ticket_number, build_id, source_version = filename.split("_")
    return int(ticket_number), build_id, source_version


def _wait_for_other_builds(files, ticket_number):
    sorted_files = _list_tickets()

    print("build queue status:")
    print()

    for order, file in enumerate(sorted_files):
        file_ticket_number, build_id, source_version = _build_info_from_file(file)
        print(
            "%s -> %s %s, ticket number: %s" % (order, build_id, source_version, file_ticket_number)
        )

    build_id = os.environ.get("CODEBUILD_BUILD_ID", "CODEBUILD-BUILD-ID")
    source_version = os.environ.get("CODEBUILD_SOURCE_VERSION", "CODEBUILD-SOURCE-VERSION").replace(
        "/", "-"
    )
    filename = "%s_%s_%s" % (ticket_number, build_id, source_version)
    _write_ticket(filename, status="waiting")
    print("Created queue ticket %s in waiting" % ticket_number)

    while True:
        _cleanup_tickets_with_terminal_states()
        first_waiting_ticket_number, _, _ = _build_info_from_file(_list_tickets("waiting")[0])
        last_in_progress_ticket, _, _ = _build_info_from_file(_list_tickets("in-progress")[-1])
        _elapsed_time = int(1000 * time.time()) - last_in_progress_ticket
        last_in_progress_elapsed_time = int(_elapsed_time / (1000 * 60))  # in minutes
        if (
            len(_list_tickets(status="in-progress")) < 3
            and last_in_progress_elapsed_time > INTERVAL_BETWEEN_CONCURRENT_RUNS
            and first_waiting_ticket_number == ticket_number
        ):
            # put the build in progress
            _delete_ticket(filename, status="waiting")
            _write_ticket(filename, status="in-progress")
            break
        else:
            # wait
            time.sleep(30)


def _cleanup_tickets_with_terminal_states():
    files = _list_tickets()
    for file in files:
        _, build_id, _ = _build_info_from_file(file)
        client = boto3.client("codebuild")
        response = client.batch_get_builds(ids=[build_id])
        build_status = response["builds"][0]["buildStatus"]

        if build_status != "IN_PROGRESS":
            print("build %s in terminal state: %s, deleting lock" % build_id, build_status)
            file.delete()


def _cleanup_tickets_older_than(files):
    oldfiles = list(filter(_file_older_than, files))
    for file in oldfiles:
        print("object %s older than 8 hours. Deleting" % file.key)
        file.delete()
    return files


def _list_tickets(status=None):
    s3 = boto3.resource("s3")
    bucket = s3.Bucket(bucket_name)
    prefix = "ci-integ-queue/{}/".format(status) if status else "ci-integ-queue/"
    objects = [file for file in bucket.objects.filter(Prefix=prefix)]
    files = list(filter(lambda x: x != prefix, objects))
    sorted_files = list(sorted(files, key=lambda y: y.key))
    return sorted_files


def _file_older_than(file):
    timelimit = 1000 * 60 * 60 * CLEAN_UP_TICKETS_OLDER_THAN
    file_ticket_number, build_id, source_version = _build_info_from_file(file)
    return int(1000 * time.time()) - file_ticket_number > timelimit


def _delete_ticket(filename, status="waiting"):
    file_path = "ci-integ-queue/{}".format(status)
    file_full_path = file_path + "/" + filename
    boto3.Session().resource("s3").Object(bucket_name, file_full_path).delete()


def _write_ticket(filename, status="waiting"):

    file_path = "ci-integ-queue/{}".format(status)
    if not os.path.exists(file_path):
        os.makedirs(file_path)

    file_full_path = file_path + "/" + filename
    with open(file_full_path, "w") as file:
        file.write(filename)
    boto3.Session().resource("s3").Object(bucket_name, file_full_path).upload_file(file_full_path)


if __name__ == "__main__":
    queue_build()

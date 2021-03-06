#!/usr/bin/python
#
# ovirt-engine-hosts-ansible-inventory
#
# An inventory script for ansible, providing oVirt hosts.
#
# There are two main differences between this script and other
# similar scripts that can be found currently on the Internet:
#
# 1. It provides hosts (hypervisors), not VMs
# There is no principle reason why this won't be extended in the future,
# but the script was writen currently to allow provisioning packages and
# configuration for collecting metrics from hosts.
# 2. It accesses the engine's db directly, using the crendentials already
# configured for the engine, and not using the API/SDK. This has the advantage
# that we do not have to prompt/get/save credentials to access the API, and
# the disadvantage that it only works on the engine machine.
#


import argparse
from collections import defaultdict
import datetime
import decimal
import gettext
import json
import os
import sys


from otopi import base


from ovirt_engine_setup.engine_common import database
from ovirt_engine_setup.engine import constants as oenginecons


from ovirt_engine import configfile


def _(m):
    return gettext.dgettext(message=m, domain='ovirt-engine-setup')


class DBFakePlugin(base.Base):
    # Just-Enough-Plugin for our needs

    def __init__(self):
        super(DBFakePlugin, self).__init__()
        self.environment = {}

    def connect_to_engine_db(self):
        statement = None
        dbovirtutils = database.OvirtUtils(
            plugin=self,
            dbenvkeys=oenginecons.Const.ENGINE_DB_ENV_KEYS,
        )
        config = configfile.ConfigFile([
            oenginecons.FileLocations.OVIRT_ENGINE_SERVICE_CONFIG_DEFAULTS,
            oenginecons.FileLocations.OVIRT_ENGINE_SERVICE_CONFIG
        ])
        if config.get('ENGINE_DB_PASSWORD'):
            try:
                dbenv = {}
                for e, k in (
                    (oenginecons.EngineDBEnv.HOST, 'ENGINE_DB_HOST'),
                    (oenginecons.EngineDBEnv.PORT, 'ENGINE_DB_PORT'),
                    (oenginecons.EngineDBEnv.USER, 'ENGINE_DB_USER'),
                    (oenginecons.EngineDBEnv.PASSWORD, 'ENGINE_DB_PASSWORD'),
                    (oenginecons.EngineDBEnv.DATABASE, 'ENGINE_DB_DATABASE'),
                ):
                    dbenv[e] = config.get(k)
                for e, k in (
                    (oenginecons.EngineDBEnv.SECURED, 'ENGINE_DB_SECURED'),
                    (
                        oenginecons.EngineDBEnv.SECURED_HOST_VALIDATION,
                        'ENGINE_DB_SECURED_VALIDATION'
                    )
                ):
                    dbenv[e] = config.getboolean(k)

                dbovirtutils.tryDatabaseConnect(dbenv)
                self.environment.update(dbenv)
                self.environment[
                    oenginecons.EngineDBEnv.NEW_DATABASE
                ] = dbovirtutils.isNewDatabase()
                statement = database.Statement(
                    dbenvkeys=oenginecons.Const.ENGINE_DB_ENV_KEYS,
                    environment=self.environment,
                )
            except RuntimeError as e:
                self.logger.debug(
                    'Existing credential use failed',
                    exc_info=True,
                )
        return statement


def parse_args():
    parser = argparse.ArgumentParser(
        description='oVirt hosts inventory script for Ansible'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        default=True,
        help='List hosts (default: True)'
    )
    parser.add_argument(
        '--host',
        action='store',
        help='Get information about a specific host'
    )
    parser.add_argument(
        '--pretty',
        action='store_true',
        default=False,
        help='Pretty-print (default: False)'
    )
    return parser.parse_args()


def get_hosts_rows(statement, host_name=None):
    args = {}
    where = ''
    if host_name:
        args['host_name'] = host_name
        where = ' WHERE host_name = %(host_name)s'
    return statement.execute(
        statement='SELECT * FROM vds%s' % where,
        args=args,
        ownConnection=True,
    )


def formatted_value(v):
    # simplejson natively support Decimal, but is not built into recent
    # python. Handle Decimal specifically here, which we get from the
    # database for values of type 'numeric'.
    return (
        str(v)
        if (
            type(v) is decimal.Decimal
            or type(v) is datetime.datetime
        )
        else v
    )


def row_to_dict(row):
    return {
        "ovirt_vds_%s" % k: formatted_value(v)
        for k, v in row.items()
    }


UP_HOSTS_GROUP_NAME = 'ovirt_up_hosts'
METRICS_HOSTS_GROUP_NAME = 'ovirt_metrics_hosts'
UP_METRICS_HOSTS_GROUP_NAME = 'ovirt_up_metrics_hosts'


def get_groups(rows):
    groups = defaultdict(list)
    groups['_meta'] = {"hostvars": {}}

    # Always have these groups, even if empty:
    groups[UP_HOSTS_GROUP_NAME] = []
    groups[METRICS_HOSTS_GROUP_NAME] = []
    groups[UP_METRICS_HOSTS_GROUP_NAME] = []

    for row in rows:
        host_name = row['host_name']
        groups['_meta']["hostvars"][host_name] = row_to_dict(row)

        cluster = row['cluster_name']
        groups['ovirt_cluster_%s' % cluster].append(host_name)

        datacenter = row['storage_pool_name']
        groups['ovirt_datacenter_%s' % datacenter].append(host_name)

        if row['status'] == 3:
            # '3' is Up. See also:
            # backend/manager/modules/common/src/main/java/org/ovirt/engine/
            # core/common/businessentities/VDSStatus.java
            groups[UP_HOSTS_GROUP_NAME].append(host_name)

        if row['cluster_compatibility_version'] >= '4.1':
            # We want to collect metrics only from 4.1 or later hosts
            groups[METRICS_HOSTS_GROUP_NAME].append(host_name)

        if (
            (row['status'] == 3) and
            (row['cluster_compatibility_version'] >= '4.1')
        ):
            # Can't easily do this intersection in ansible itself, so doing
            # it here for now. See also:
            # https://github.com/ansible/ansible/issues/10131
            # Add Dynamic group intersections to inventory file
            groups[UP_METRICS_HOSTS_GROUP_NAME].append(host_name)

    return groups


def main():
    args = parse_args()
    statement = DBFakePlugin().connect_to_engine_db()
    result = {}
    if statement:
        if args.host:
            rows = get_hosts_rows(statement, args.host)
            if rows:
                result = row_to_dict(rows[0])
        else:
            rows = get_hosts_rows(statement)
            result = get_groups(rows)
    print(
        json.dumps(
            obj=result,
            sort_keys=args.pretty,
            indent=(4 if args.pretty else None),
        )
    )


if __name__ == '__main__':
    main()


# vim: expandtab tabstop=4 shiftwidth=4

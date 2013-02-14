#
# Copyright (C) 2013 Nexenta Systems, Inc.
# All rights reserved.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Author: legkodymov

import urllib2
import urlparse
import simplejson as json
import base64

from iplugin import INfs, IStorageAreaNetwork
from data import Pool, FileSystem, Snapshot, Capabilities, System, \
    NfsExport, Volume, Initiator, AccessGroup
from common import LsmError, ErrorNumber, md5


class NexentaStor(INfs, IStorageAreaNetwork):
    def __init__(self):
        self.uparse = None
        self.password = None
        self.timeout = None

    def _ns_request(self, path, data):
        data = json.dumps(data)
        scheme = 'http'
        if self.uparse.scheme.lower() == 'nstor+ssl':
            scheme = 'https'
        port = self.uparse.port or '2000'
        url = '%s://%s:%s/%s' % (scheme, self.uparse.hostname, port, path)
        request = urllib2.Request(url, data)

        username = self.uparse.username or 'admin'
        base64string = base64.encodestring('%s:%s' %
                                           (username, self.password))[:-1]
        request.add_header('Authorization', 'Basic %s' % base64string)
        request.add_header('Content-Type', 'application/json')
        try:
            response = urllib2.urlopen(request, timeout=self.timeout / 1000)
        except urllib2.URLError as e:
            raise LsmError(ErrorNumber.PLUGIN_ERROR, str(e))
        resp_json = response.read()
        resp = json.loads(resp_json)
        if resp['error']:
            raise LsmError(ErrorNumber.PLUGIN_ERROR, resp['error'])
        return resp['result']

    def startup(self, uri, password, timeout, flags=0):
        self.uparse = urlparse.urlparse(uri)
        self.password = password or 'nexenta'
        self.timeout = timeout

    def _to_bytes(self, size):
        if size.lower().endswith('k'):
            return int(float(size[:-1]) * 1024)
        if size.lower().endswith('m'):
            return int(float(size[:-1]) * 1024 * 1024)
        if size.lower().endswith('g'):
            return int(float(size[:-1]) * 1024 * 1024 * 1024)
        if size.lower().endswith('t'):
            return int(float(size[:-1]) * 1024 * 1024 * 1024 * 1024)
        if size.lower().endswith('p'):
            return int(float(size[:-1]) * 1024 * 1024 * 1024 * 1024 * 1024)
        if size.lower().endswith('e'):
            return int(
                float(size[:-1]) * 1024 * 1024 * 1024 * 1024 * 1024 * 1024)
        return size

    def pools(self, flags=0):
        pools_list = self._ns_request('rest/nms', {"method": "get_all_names",
                                                   "object": "volume",
                                                   "params": [""]})
        pools = []
        for pool in pools_list:
            if pool == 'syspool':
                continue
            pool_info = self._ns_request('rest/nms',
                                         {"method": "get_child_props",
                                          "object": "volume",
                                          "params": [str(pool), ""]})
            pools.append(Pool(pool_info['name'], pool_info['name'],
                              self._to_bytes(pool_info['size']),
                              self._to_bytes(pool_info['free']),
                              pool_info['guid']))

        return pools

    def fs(self, flags=0):
        fs_list = self._ns_request('rest/nms', {"method": "get_all_names",
                                                "object": "folder",
                                                "params": [""]})
        fss = []
        pools = {}
        for fs in fs_list:
            pool_name = self._get_pool_id(fs)
            if pool_name == 'syspool':
                continue
            if not pool_name in pools:
                pool_info = self._ns_request('rest/nms',
                                             {"method": "get_child_props",
                                              "object": "volume",
                                              "params": [str(fs), ""]})
                pools[pool_name] = pool_info
            else:
                pool_info = pools[pool_name]
            fss.append(FileSystem(fs, fs,
                                  self._to_bytes(pool_info['size']),
                                  self._to_bytes(pool_info['available']),
                                  pool_name,
                                  fs))

        return fss

    def fs_create(self, pool, name, size_bytes, flags=0):
        """
        Consider you have 'data' pool and folder 'a' in it (data/a)
        If you want create 'data/a/b', command line should look like:
        --create-fs=a/b --pool=data --size=1G
        """
        if name.startswith(pool.name + '/'):
            chunks = name.split('/')[1:]
            name = '/'.join(chunks)
        fs_name = self._ns_request('rest/nms', {"method": "create",
                                                "object": "folder",
                                                "params": [pool.name,
                                                           name]})
        filesystem = FileSystem(fs_name, fs_name, pool.total_space,
                                pool.free_space, pool.id, fs_name)
        return None, filesystem

    def fs_delete(self, fs, flags=0):
        result = self._ns_request('rest/nms', {"method": "destroy",
                                               "object": "folder",
                                               "params": [fs.name, "-r"]})
        return

    def fs_snapshots(self, fs, flags=0):
        snapshot_list = self._ns_request('rest/nms',
                                         {"method": "get_names",
                                          "object": "snapshot",
                                          "params": [fs.name]})
        snapshots = []
        for snapshot in snapshot_list:
            snapshot_info = self._ns_request('rest/nms',
                                             {"method": "get_child_props",
                                              "object": "snapshot",
                                              "params": [snapshot,
                                                         "creation_seconds"]})
            snapshots.append(Snapshot(snapshot, snapshot,
                                      snapshot_info['creation_seconds']))

        return snapshots

    def fs_snapshot_create(self, fs, snapshot_name, files, flags=0):
        full_name = "%s@%s" % (fs.name, snapshot_name)
        self._ns_request('rest/nms',
                         {"method": "create",
                          "object": "snapshot",
                          "params": [full_name, "0"]})
        snapshot_info = self._ns_request('rest/nms',
                                         {"method": "get_child_props",
                                          "object": "snapshot",
                                          "params": [full_name,
                                                     "creation_seconds"]})
        return None, Snapshot(full_name, full_name,
                              snapshot_info['creation_seconds'])

    def fs_snapshot_delete(self, fs, snapshot, flags=0):
        result = self._ns_request('rest/nms', {"method": "destroy",
                                               "object": "snapshot",
                                               "params": [snapshot.name, ""]})
        return

    def set_time_out(self, ms, flags=0):
        self.timeout = ms
        return

    def get_time_out(self, flags=0):

        return self.timeout

    def shutdown(self, flags=0):
        return

    def job_status(self, job_id, flags=0):
        return

    def job_free(self, job_id, flags=0):
        return

    def capabilities(self, system, flags=0):
        c = Capabilities()

        #Array wide
        #        c.set(Capabilities.BLOCK_SUPPORT)
        c.set(Capabilities.FS_SUPPORT)

        #File system
        c.set(Capabilities.FS)
        c.set(Capabilities.FS_DELETE)
        c.set(Capabilities.FS_RESIZE)
        c.set(Capabilities.FS_CREATE)
        c.set(Capabilities.FS_CLONE)
        #        c.set(Capabilities.FILE_CLONE)
        c.set(Capabilities.FS_SNAPSHOTS)
        c.set(Capabilities.FS_SNAPSHOT_CREATE)
        #        c.set(Capabilities.FS_SNAPSHOT_CREATE_SPECIFIC_FILES)
        c.set(Capabilities.FS_SNAPSHOT_DELETE)
        c.set(Capabilities.FS_SNAPSHOT_REVERT)
        #        c.set(Capabilities.FS_SNAPSHOT_REVERT_SPECIFIC_FILES)
        c.set(Capabilities.FS_CHILD_DEPENDENCY)
        c.set(Capabilities.FS_CHILD_DEPENDENCY_RM)
        #        c.set(Capabilities.FS_CHILD_DEPENDENCY_RM_SPECIFIC_FILES)

        #        #NFS
        c.set(Capabilities.EXPORT_AUTH)
        c.set(Capabilities.EXPORTS)
        c.set(Capabilities.EXPORT_FS)
        c.set(Capabilities.EXPORT_REMOVE)
        #
        #        #Block operations
        c.set(Capabilities.VOLUMES)
        c.set(Capabilities.VOLUME_CREATE)
        c.set(Capabilities.VOLUME_RESIZE)
        #        c.set(Capabilities.VOLUME_REPLICATE)
        #        c.set(Capabilities.VOLUME_REPLICATE_CLONE)
        #        c.set(Capabilities.VOLUME_REPLICATE_COPY)
        #        c.set(Capabilities.VOLUME_REPLICATE_MIRROR_ASYNC)
        #        c.set(Capabilities.VOLUME_REPLICATE_MIRROR_SYNC)
        #        c.set(Capabilities.VOLUME_COPY_RANGE_BLOCK_SIZE)
        #        c.set(Capabilities.VOLUME_COPY_RANGE)
        #        c.set(Capabilities.VOLUME_COPY_RANGE_CLONE)
        #        c.set(Capabilities.VOLUME_COPY_RANGE_COPY)
        c.set(Capabilities.VOLUME_DELETE)
        #        c.set(Capabilities.VOLUME_ONLINE)
        #        c.set(Capabilities.VOLUME_OFFLINE)
        c.set(Capabilities.ACCESS_GROUP_GRANT)
        c.set(Capabilities.ACCESS_GROUP_REVOKE)
        c.set(Capabilities.ACCESS_GROUP_LIST)
        c.set(Capabilities.ACCESS_GROUP_CREATE)
        c.set(Capabilities.ACCESS_GROUP_DELETE)
        c.set(Capabilities.ACCESS_GROUP_ADD_INITIATOR)
        c.set(Capabilities.ACCESS_GROUP_DEL_INITIATOR)
        c.set(Capabilities.VOLUMES_ACCESSIBLE_BY_ACCESS_GROUP)
        c.set(Capabilities.ACCESS_GROUPS_GRANTED_TO_VOLUME)
        c.set(Capabilities.VOLUME_CHILD_DEPENDENCY)
        c.set(Capabilities.VOLUME_CHILD_DEPENDENCY_RM)
        c.set(Capabilities.INITIATORS)
        c.set(Capabilities.INITIATORS_GRANTED_TO_VOLUME)
        c.set(Capabilities.VOLUME_INITIATOR_GRANT)
        c.set(Capabilities.VOLUME_INITIATOR_REVOKE)
        c.set(Capabilities.VOLUME_ACCESSIBLE_BY_INITIATOR)
        c.set(Capabilities.VOLUME_ISCSI_CHAP_AUTHENTICATION)

        return c

    def systems(self, flags=0):
        license_info = self._ns_request('rest/nms',
                                        {"method": "get_license_info",
                                         "object": "appliance",
                                         "params": [""]})
        fqdn = self._ns_request('rest/nms',
                                {"method": "get_fqdn",
                                 "object": "appliance",
                                 "params": [""]})
        self.system = System(license_info['machine_sig'], fqdn,
                             System.STATUS_OK)
        return [self.system]

    def fs_resize(self, fs, new_size_bytes, flags=0):
        return

    def _get_pool_id(self, fs_name):
        return fs_name.split('/')[0]

    def fs_clone(self, src_fs, dest_fs_name, snapshot=None, flags=0):
        if snapshot is None:
            raise LsmError(ErrorNumber.INVALID_SS,
                           "Full snapshot name (i.e data/a@A) is mandatory for "
                           "NexentaStor.")
        self._ns_request('rest/nms', {"method": "clone",
                                      "object": "folder",
                                      "params": [snapshot.name,
                                                 dest_fs_name]})
        pool_id = self._get_pool_id(dest_fs_name)
        pool_info = self._ns_request('rest/nms',
                                     {"method": "get_child_props",
                                      "object": "volume",
                                      "params": [pool_id, ""]})
        fs = FileSystem(dest_fs_name, dest_fs_name,
                        self._to_bytes(pool_info['size']),
                        self._to_bytes(pool_info['available']), pool_id,
                        dest_fs_name)
        return None, fs

    def file_clone(self, fs, src_file_name, dest_file_name, snapshot=None,
                   flags=0):
        return

    def fs_snapshot_revert(self, fs, snapshot, files, restore_files,
                           all_files=False, flags=0):
        self._ns_request('rest/nms', {"method": "rollback",
                                      "object": "snapshot",
                                      "params": [snapshot.name, '-r']})

        return

    def _dependencies_list(self, fs_name, volume=False):
        obj = "folder"
        if volume:
            obj = 'volume'
        pool_id = self._get_pool_id(fs_name)
        fs_list = self._ns_request('rest/nms', {"method": "get_all_names",
                                                "object": "folder",
                                                "params": ["^%s/" % pool_id]})
        dependency_list = []
        for filesystem in fs_list:
            origin = self._ns_request('rest/nms', {"method": "get_child_prop",
                                                   "object": "folder",
                                                   "params": [filesystem,
                                                              'origin']})
            if origin.startswith("%s/" % fs_name) or \
                    origin.startswith("%s@" % fs_name):
                dependency_list.append(filesystem)
        return dependency_list

    def fs_child_dependency(self, fs, files, flags=0):
        # Function get list of all folders of requested pool,
        # then it checks if 'fs' is the origin of one of folders
        return len(self._dependencies_list(fs.name)) > 0

    def fs_child_dependency_rm(self, fs, files, flags=0):
        dep_list = self._dependencies_list(fs.name)
        for dep in dep_list:
            clone_name = dep.split('@')[0]
            self._ns_request('rest/nms', {"method": "promote",
                                          "object": "folder",
                                          "params": [clone_name]})
        return None

    def export_auth(self, flags=0):
        """
        Returns the types of authentication that are available for NFS
        """
        result = self._ns_request('rest/nms',
                                  {"method": "get_share_confopts",
                                   "object": "netstorsvc", "params":
                                      ['svc:/network/nfs/server:default']})
        return [result['auth_type']['opts']]

    def exports(self, flags=0):
        """
        Get a list of all exported file systems on the controller.
        """
        exp_list = self._ns_request('rest/nms', {"method": "get_shared_folders",
                                    "object": "netstorsvc",
                                    "params": [
                                    'svc:/network/nfs/server:default', '']})
        exports = []
        for e in exp_list:
            opts = self._ns_request('rest/nms',
                                    {"method": "get_shareopts",
                                     "object": "netstorsvc",
                                     "params": [
                                         'svc:/network/nfs/server:default', e]})
            exports.append(NfsExport(md5(opts['name']),
                                     e, opts['name'], opts['auth_type'],
                                     opts['root'],
                                     opts['read_write'], opts['read_only'],
                                     'N/A', 'N/A',
                                     opts['extra_options']))

        return exports

    def export_fs(self, fs_id, export_path, root_list, rw_list, ro_list,
                  anon_uid, anon_gid, auth_type, options, flags=0):
        """
        Exports a filesystem as specified in the export
        """
        md5_id = md5(export_path)
        fs_dict = {'auth_type': 'sys', 'anonymous': 'false'}
        if ro_list:
            fs_dict['read_only'] = ','.join(ro_list)
        if rw_list:
            fs_dict['read_write'] = ','.join(rw_list)
        if anon_uid or anon_gid:
            fs_dict['anonymous'] = 'true'
        if root_list:
            fs_dict['root'] = ','.join(root_list)
        if auth_type:
            fs_dict['auth_type'] = str(auth_type)
        if '*' in rw_list:
            fs_dict['anonymous'] = 'true'
        if options:
            fs_dict['extra_options'] = str(options)
        result = self._ns_request('rest/nms',
                                  {"method": "share_folder",
                                   "object": "netstorsvc",
                                   "params": ['svc:/network/nfs/server:default',
                                              fs_id, fs_dict]})
        return NfsExport(md5_id, fs_id, export_path, auth_type,
                         root_list, rw_list, ro_list, anon_uid, anon_gid,
                         options)

    def export_remove(self, export, flags=0):
        """
        Removes the specified export
        """
        self._ns_request('rest/nms',
                         {"method": "unshare_folder",
                          "object": "netstorsvc",
                          "params": ['svc:/network/nfs/server:default',
                                     export.fs_id, '0']})
        return

    ###########  SAN

    def _calc_group(self, name):
        return 'lsm_' + md5(name)[0:8]

    def volumes(self, flags=0):
        """
        Returns an array of volume objects

        """
        vol_list = []
        lu_list = self._ns_request('rest/nms', {"method": "get_names",
                                                "object": "zvol",
                                                "params": [""]})
        #        lu_list = self._ns_request('rest/nms',
        #            {"method": "get_lu_list",
        #             "object": "scsidisk",
        #             "params": ['']})
        for lu in lu_list:
            try:
                lu_props = self._ns_request('rest/nms',
                                            {"method": "get_lu_props",
                                             "object": "scsidisk",
                                             "params": [lu]})
            except:
                lu_props = {'guid': 'N/A', 'state': 'N/A'}
            zvol_props = self._ns_request('rest/nms',
                                          {"method": "get_child_props",
                                           "object": "zvol",
                                           "params": [lu, ""]})
            block_size = self._to_bytes(zvol_props['volblocksize'])
            size_bytes = int(zvol_props['size_bytes'])
            num_of_blocks = size_bytes / block_size
            vol_list.append(Volume(lu, lu, lu_props['guid'],
                                   block_size, num_of_blocks,
                                   lu_props['state'],
                                   'N/A',
                                   self._get_pool_id(lu)))

        return vol_list

    def initiators(self, flags=0):
        """
        Return an array of initiator objects
        """
        i_list = []
        for ag in self.access_group_list():
            for initiator_id in ag.initiators:
                i_list.append(Initiator(initiator_id,
                                        "TYPE_ISCSI", initiator_id))
        return i_list

    def volume_create(self, pool, volume_name, size_bytes, provisioning,
                      flags=0):
        """
        Creates a volume, given a pool, volume name, size and provisioning

        returns a tuple (job_id, new volume)
        Note: Tuple return values are mutually exclusive, when one
        is None the other must be valid.
        """
        if volume_name.startswith(pool.name + '/'):
            chunks = volume_name.split('/')[1:]
            volume_name = '/'.join(chunks)
        sparse = provisioning in (Volume.PROVISION_DEFAULT,
                                  Volume.PROVISION_THIN,
                                  Volume.PROVISION_UNKNOWN)
        if sparse:
            sparse = '1'
        else:
            sparse = '0'
        name = '%s/%s' % (pool.name, volume_name)
        block_size = ''
        self._ns_request('rest/nms',
                         {"method": "create",
                          "object": "zvol",
                          "params": [name, str(size_bytes),
                                     block_size,
                                     sparse]})
        self._ns_request('rest/nms',
                         {"method": "set_child_prop",
                          "object": "zvol",
                          "params": [name, 'compression', 'on']})
        self._ns_request('rest/nms',
                         {"method": "set_child_prop",
                          "object": "zvol",
                          "params": [name, 'logbias', 'throughput']})
        self._ns_request('rest/nms',
                         {"method": "create_lu",
                          "object": "scsidisk",
                          "params": [name, []]})

        new_volume = Volume(name, name, '', 8192, size_bytes / 8192, '', '',
                            pool.id)    # FIXhttp://192.168.0.1/st_wlan.phpME
                                        # replace with list request
        return None, new_volume

    def volume_delete(self, volume, flags=0):
        """
        Deletes a volume.

        Returns None on success, else raises an LsmError
        """
        self._ns_request('rest/nms',
                         {"method": "delete_lu",
                          "object": "scsidisk",
                          "params": [volume.id]})
        self._ns_request('rest/nms',
                         {"method": "destroy",
                          "object": "zvol",
                          "params": [volume.id, '']})
        return

    def volume_resize(self, volume, new_size_bytes, flags=0):
        """
        Re-sizes a volume.

        Returns a tuple (job_id, re-sized_volume)
        Note: Tuple return values are mutually exclusive, when one
        is None the other must be valid.
        """
        self._ns_request('rest/nms', {"method": "set_child_prop",
                                      "object": "zvol",
                                      "params": [volume.name, 'volsize',
                                                 str(new_size_bytes)]})
        self._ns_request('rest/nms', {"method": "realign_size",
                                      "object": "scsidisk",
                                      "params": [volume.name]})
        new_num_of_blocks = new_size_bytes / volume.block_size
        return None, Volume(volume.id, volume.name, volume.vpd83,
                            volume.block_size, new_num_of_blocks, volume.status,
                            volume.system_id, volume.pool_id)

    def volume_replicate(self, pool, rep_type, volume_src, name, flags=0):
        """
        Replicates a volume from the specified pool.  In this library, to
        replicate means to create a new volume which is a copy of the source.

        Returns a tuple (job_id, replicated volume)
        Note: Tuple return values are mutually exclusive, when one
        is None the other must be valid.
        """
        if rep_type == Volume.REPLICATE_SNAPSHOT:
            return
        elif rep_type == Volume.REPLICATE_CLONE:
            return
        elif rep_type == Volume.REPLICATE_COPY:
            return
        elif rep_type == Volume.REPLICATE_MIRROR_SYNC:
            return
        elif rep_type == Volume.REPLICATE_MIRROR_ASYNC:
    #            # AutoSync job - code not yet ready
    #            rec = {'type': 'minute',	'auto-mount': '', 'dircontent': '0',
    #                'direction': '0', 'keep_src': '1',	'keep_dst': '1',
    #                'auto-clone': '0', 'marker': '', 'method': 'sync',
    #                'proto': 'zfs',	'period': '1',	'exclude': '',
    #                'from-host': 'localhost', 'from-fs': str(volume_src.name),
    #                'to-host': 'localhost',	'to-fs': '/backup',
    #                'progress-marker': '', 'day': '0',
    #                'hour': '0',	'minute': '0',	'options': ' -P1024 -n4',
    #                'from-snapshot': '', 'force': '46', 'retry': '0',
    #                'retry-timestamp': '0',	'comment': '', 'flags': '4',
    #                'trace_level': '10', 'rate_limit': '0',
    #                'autosync_role': 'master:no', 'action': '',
    #                'reverse_capable': '0',	'last_replic_time': '',
    #                'time_started': 'N/A',
    #                '_unique': 'type from-host from-fs to-host to-fs',
    #                'zip_level': '0', 'success_counter': '0',	'trunk': '',
    #                'estimations': '0',	'marker_name': "AutoSync",
    #                'latest-suffix': '',	'custom_name': ''}
    #            ret = self._ns_request('rest/nms', {"method": "fmri_create",
    #                                          "object": "autosvc",
    #                                          "params": ['auto-sync', '',
    #                                                     str(volume_src.name),
    #                                                     False, rec]})
            return
        elif rep_type == Volume.REPLICATE_UNKNOWN:
            return

        return

    def volume_replicate_range_block_size(self, system, flags=0):
        """
        Returns the number of bytes per block for volume_replicate_range
        call.  Callers of volume_replicate_range need to use this when
        calculating start and block lengths.

        Note: bytes per block may not match volume blocksize.

        Returns bytes per block.
        """
        raise LsmError(ErrorNumber.NO_SUPPORT, "This operation is not "
                                               "supported")

    def volume_replicate_range(self, rep_type, volume_src, volume_dest, ranges,
                               flags=0):
        """
        Replicates a portion of a volume to itself or another volume.  The src,
        dest and number of blocks values change with vendor, call
        volume_replicate_range_block_size to get block unit size.

        Returns Job id or None if completed, else raises LsmError on errors.
        """
        raise LsmError(ErrorNumber.NO_SUPPORT, "Replicate range operation is"
                                               " not supported")

    def volume_online(self, volume, flags=0):
        return

    def volume_offline(self, volume, flags=0):
        return

    def iscsi_chap_auth_inbound(self, initiator, user, password, flags=0):
        """
        Register a user/password for the specified initiator for CHAP
        authentication.
        """
        try:
            ret = self._ns_request('rest/nms',
                                   {"method": "create_initiator",
                                    "object": "iscsitarget",
                                    "params": [initiator.name,
                                               {'initiatorchapuser': user,
                                                'initiatorchapsecret':
                                                password}]})
        except:
            ret = self._ns_request('rest/nms',
                                   {"method": "modify_initiator",
                                    "object": "iscsitarget",
                                    "params": [initiator.name,
                                               {'initiatorchapuser': user,
                                               'initiatorchapsecret':
                                                password}]})
        return

    def initiator_grant(self, initiator_id, initiator_type, volume, access,
                        flags=0):
        """
        Allows an initiator to access a volume.
        """
        hg_name = self._calc_group(initiator_id)
        try:
            self.access_group_create(hg_name, initiator_id, initiator_type,
                                     'NA')
        except:
            pass
        self._access_group_grant(hg_name, volume.name, access)
        return

    def initiator_revoke(self, initiator, volume, flags=0):
        """
        Revokes access to a volume for the specified initiator
        """
        ag_name = self._calc_group(initiator.name)
        views = self._ns_request('rest/nms',
                                 {"method": "list_lun_mapping_entries",
                                  "object": "scsidisk",
                                  "params": [volume.name]})
        view_number = -1
        for view in views:
            if view['host_group'] == ag_name:
                view_number = view['entry_number']
        if view_number == -1:
            raise LsmError(ErrorNumber.NO_MAPPING, "There is no such mapping "
                                                   "for volume %s" %
                                                   volume.name)
        self._ns_request('rest/nms',
                         {"method": "remove_lun_mapping_entry",
                          "object": "scsidisk",
                          "params": [volume.name, view_number]})
        self._ns_request('rest/nms',
                         {"method": "destroy_hostgroup",
                          "object": "stmf",
                          "params": [ag_name]})
        return

    def _list_view(self, group, volume):
        ret = self._ns_request('rest/nms',
                               {"method": "list_lun_mapping_entries",
                                "object": "scsidisk",
                                "params": [volume.name]})
        return

    def _access_group_grant(self, group_name, volume_name, access):
        ret = self._ns_request('rest/nms',
                               {"method": "add_lun_mapping_entry",
                                "object": "scsidisk",
                                "params": [volume_name,
                                           {'host_group': group_name}]})
        return

    def access_group_grant(self, group, volume, access, flags=0):
        """
        Allows an access group to access a volume.
        """
        self._access_group_grant(group.name, volume.name, access)
        return

    def access_group_revoke(self, group, volume, flags=0):
        """
        Revokes access for an access group for a volume
        """
        views = self._ns_request('rest/nms',
                                 {"method": "list_lun_mapping_entries",
                                  "object": "scsidisk",
                                  "params": [volume.name]})
        view_number = -1
        for view in views:
            if view['host_group'] == group.name:
                view_number = view['entry_number']
        if view_number == -1:
            raise LsmError(ErrorNumber.NO_MAPPING, "There is no such mapping "
                                                   "for volume %s" %
                                                   volume.name)
        ret = self._ns_request('rest/nms',
                               {"method": "remove_lun_mapping_entry",
                               "object": "scsidisk",
                               "params": [volume.name, view_number]})
        return

    def access_group_list(self, flags=0):
        """
        Returns a list of access groups
        """
        ag_list = self._ns_request('rest/nms',
                                   {"method": "list_hostgroups",
                                   "object": "stmf",
                                   "params": []})
        ag_list = []
        for hg in ag_list:
            initiators = self._ns_request('rest/nms',
                                          {"method": "list_hostgroup_members",
                                           "object": "stmf",
                                           "params": [hg]})
            ag_list.append(AccessGroup(hg, hg, initiators))
        return ag_list

    def _initiator_human_type(self, init_id):
        text = ['TYPE_OTHER', 'TYPE_PORT_WWN', 'TYPE_NODE_WWN',
                'TYPE_HOSTNAME', 'TYPE_ISCSI']
        return text[init_id - 1]

    def access_group_create(self, name, initiator_id, id_type, system_id,
                            flags=0):
        """
        Creates of access group
        """
        #  Check that initiator_id is not a part of another hostgroup
        for ag in self.access_group_list():
            if initiator_id in ag.initiators:
                raise LsmError(ErrorNumber.EXISTS_INITIATOR,
                               "%s is already part of %s access group" % (
                                   initiator_id,
                                   ag.name))
        self._ns_request('rest/nms',
                         {"method": "create_hostgroup",
                          "object": "stmf",
                          "params": [name]})
        self._add_initiator(name, initiator_id)
        return Initiator(initiator_id, self._initiator_human_type(id_type),
                         name)

    def access_group_del(self, group, flags=0):
        """
        Deletes an access group
        """
        self._ns_request('rest/nms',
                         {"method": "destroy_hostgroup",
                          "object": "stmf",
                          "params": [group.name]})
        return

    def _add_initiator(self, group_name, initiator_id, remove=False):
        command = "add_hostgroup_member"
        if remove:
            command = "remove_hostgroup_member"
        self._ns_request('rest/nms',
                         {"method": command,
                          "object": "stmf",
                          "params": [group_name, initiator_id]})
        return

    def access_group_add_initiator(self, group, initiator_id, id_type,
                                   flags=0):
        """
        Adds an initiator to an access group
        """
        self._add_initiator(group.name, initiator_id)
        return Initiator(initiator_id, self._initiator_human_type(id_type),
                         initiator_id)

    def access_group_del_initiator(self, group, initiator_id, flags=0):
        """
        Deletes an initiator from an access group
        """
        self._add_initiator(group.name, initiator_id, True)
        return

    def volumes_accessible_by_access_group(self, group, flags=0):
        """
        Returns the list of volumes that access group has access to.
        """
        volumes = []
        all_volumes_list = self.volumes()
        for vol in all_volumes_list:
            views = self._ns_request('rest/nms',
                                     {"method": "list_lun_mapping_entries",
                                      "object": "scsidisk",
                                      "params": [vol.name]})
            for view in views:
                if view['host_group'] == group.name:
                    volumes.append(vol)
        return volumes

    def access_groups_granted_to_volume(self, volume, flags=0):
        """
        Returns the list of access groups that have access to the specified
        """
        ag_list = self.access_group_list()
        views = self._ns_request('rest/nms',
                                 {"method": "list_lun_mapping_entries",
                                  "object": "scsidisk",
                                  "params": [volume.name]})
        hg = []
        for view in views:
            for ag in ag_list:
                if ag.name == view['host_group']:
                    hg.append(ag)
        return hg

    def volume_child_dependency(self, volume, flags=0):
        """
        Returns True if this volume has other volumes which are dependant on it.
        Implies that this volume cannot be deleted or possibly modified because
        it would affect its children.
        """
        return len(self._dependencies_list(volume.name, True)) > 0

    def volume_child_dependency_rm(self, volume, flags=0):
        """
        If this volume has child dependency, this method call will fully
        replicate the blocks removing the relationship between them.  This
        should return None (success) if volume_child_dependency would return
        False.

        Note:  This operation could take a very long time depending on the size
        of the volume and the number of child dependencies.

        Returns None if complete else job id, raises LsmError on errors.
        """
        dep_list = self._dependencies_list(volume.name)
        for dep in dep_list:
            clone_name = dep.split('@')[0]
            self._ns_request('rest/nms', {"method": "promote",
                                          "object": "volume",
                                          "params": [clone_name]})
        return None

    def volumes_accessible_by_initiator(self, initiator, flags=0):
        """
        Returns a list of volumes that the initiator has access to.
        """
        ag_name = self._calc_group(initiator.name)
        volumes = []
        all_volumes_list = self.volumes()
        for vol in all_volumes_list:
            views = self._ns_request('rest/nms',
                                     {"method": "list_lun_mapping_entries",
                                      "object": "scsidisk",
                                      "params": [vol.name]})
            for view in views:
                if view['host_group'] == ag_name:
                    volumes.append(vol)
        return volumes

    def initiators_granted_to_volume(self, volume, flags=0):
        """
        Returns a list of initiators that have access to the specified volume.
        """
        ag_list = self.access_group_list()
        i_list = self.initiators()
        initiators_id = []
        views = self._ns_request('rest/nms',
                                 {"method": "list_lun_mapping_entries",
                                  "object": "scsidisk",
                                  "params": [volume.name]})
        for view in views:
            for ag in ag_list:
                if ag.name == view['host_group']:
                    initiators_id.extend(ag.initiators)
        initiators = []
        for i in i_list:
            if i.name in initiators_id:
                initiators.append(i)
        return initiators
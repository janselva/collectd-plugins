import requests
import time
import subprocess
from subprocess import check_output
from copy import deepcopy
import collectd
import sys
from os import path
import os
import write_json
from utils import * # pylint: disable=W
sys.path.append(path.dirname(path.abspath("/opt/collectd/plugins/sf-plugins-hadoop/Collectors/configuration.py")))
sys.path.append(path.dirname(path.abspath("/opt/collectd/plugins/sf-plugins-hadoop/Collectors/hadoopClusterCollector/yarn_stats.py")))
sys.path.append(path.dirname(path.abspath("/opt/collectd/plugins/sf-plugins-hadoop/Collectors/requirements.txt")))

from configuration import *
from yarn_stats import run_application, initialize_app

class YarnStats:
    def __init__(self):
        """Plugin object will be created only once and \
           collects yarn statistics info every interval."""
        self.retries = 3
#        self.url_knox = "https://localhost:8443/gateway/default/ambari/api/v1/clusters"
        self.url_knox = "http://localhost:8080/api/v1/clusters"
        self.cluster_name = None
        self.is_config_updated = 0
        self.username = "admin"
        self.password = "MapleAdmin123$"

    def check_fields(self, line, dic_fields):
        for field in dic_fields:
            if (field+"=" in line or field+" =" in line):
                return field
        return None

    def update_config_file(self, previous_json_yarn):
        file_name = "/opt/collectd/plugins/sf-plugins-hadoop/Collectors/configuration.py"
        lines = []
        flag = 0
        previous_json_yarn = previous_json_yarn.strip(".")

        logging_config["yarn"] = logging_config["yarn"].strip(".")
        logging_config["hadoopCluster"] = logging_config["hadoopCluster"].strip(".")
        dic_fields = {"resource_manager": resource_manager,"elastic": elastic, "indices": indices, "previous_json_yarn": previous_json_yarn, "tag_app_name": tag_app_name, "logging_config": logging_config}

        with open(file_name, "r") as read_config_file:
            for line in read_config_file.readlines():
                field = self.check_fields(line, dic_fields)
                if field and ("{" in line and "}" in line):
                    lines.append("%s = %s\n" %(field, dic_fields[field]))
                elif field or flag:
                    if field:
                        if field == "previous_json_yarn":
                            lines.append('%s = "%s"\n' %(field, dic_fields[field]))
                        else:
                            lines.append("%s = %s\n" %(field, dic_fields[field]))
                    if field and "{" in line:
                        flag = 1
                    if "}" in line:
                        flag = 0
                else:
                    lines.append(line)
        read_config_file.close()
        with open(file_name, "w") as write_config:
            for line in lines:
                write_config.write(line)
        write_config.close()

    def get_elastic_search_details(self):
        try:
            with open("/opt/collectd/conf/elasticsearch.conf", "r") as file_obj:
                for line in file_obj.readlines():
                    if "URL" not in line:
                        continue
                    elastic_search = line.split("URL")[1].split("//")[1].split("/")
                    index = elastic_search[1].strip("/").strip("_doc")
                    elastic_search = elastic_search[0].split(":")
                    return elastic_search[0], elastic_search[1], index
        except IOError:
            collectd.error("Could not read file: /opt/collectd/conf/elasticsearch.conf")

    def get_app_name(self):
        try:
            with open("/opt/collectd/conf/filters.conf", "r") as file_obj:
                for line in file_obj.readlines():
                    if 'MetaData "_tag_appName"' not in line:
                        continue
                    return line.split(" ")[2].strip('"')
        except IOError:
            collectd.error("Could not read file: /opt/collectd/conf/filters.conf")

    def get_cluster(self):
        res_json = requests.get(self.url_knox, auth=(self.username, self.password), verify=False)
        if res_json.status_code != 200:
            return None
        self.cluster_name = res_json.json()["items"][0]["Clusters"]["cluster_name"]
        return self.cluster_name

    def is_service_running(self, services):
        for service in services:
            res_json = requests.get(self.url_knox+"/"+self.cluster_name+"/services/%s" %service, auth=(self.username, self.password), verify=False)
            if res_json.status_code != 200:
                collectd.error("URL is not responding for %s" %service)
                return False
            if res_json.json()["ServiceInfo"]["state"] != "INSTALLED" and res_json.json()["ServiceInfo"]["state"] != "STARTED":
                collectd.error("%s is not running" %service)
                return False
        return True



    def get_hadoop_service_details(self, url):
        res_json = requests.get(url, auth=(self.username, self.password), verify=False)
        if res_json.status_code != 200:
            collectd.error("Couldn't get history_server details")
            return None
        lst_servers = []
        res_json = res_json.json()
        for host_component in res_json["host_components"]:
            lst_servers.append(host_component["HostRoles"]["host_name"])
        return lst_servers

    def read_config(self, cfg):
        """Initializes variables from conf files."""
        for children in cfg.children:
            if children.key == INTERVAL:
                self.interval = children.values[0]
            elif children.key == USER:
                self.username = children.values[0]
            elif children.key == PASSWORD:
                self.password = children.values[0]

        host, port, index = self.get_elastic_search_details()
        elastic["host"] = host
        elastic["port"] = port
        indices["yarn"] = index
        appname = self.get_app_name()
        tag_app_name['yarn'] = appname
        resource_manager["port"] = "8088"
        self.cluster_name = self.get_cluster()
        if self.cluster_name and self.is_service_running(["YARN"]):
            hosts = self.get_hadoop_service_details(self.url_knox+"/"+self.cluster_name+"/services/YARN/components/RESOURCEMANAGER")
            if hosts:
                resource_manager["hosts"] = hosts
                self.update_config_file(previous_json_yarn)
                self.is_config_updated = 1
                initialize_app()
            else:
                collectd.error("Unable to get yarn hosts")
        else:
            collectd.error("Unable to get cluster name")


    @staticmethod
    def add_common_params(namenode_dic, doc_type):
        """Adds TIMESTAMP, PLUGIN, PLUGIN_INS to dictionary."""
        hostname = gethostname()
        timestamp = int(round(time.time()))

        namenode_dic[HOSTNAME] = hostname
        namenode_dic[TIMESTAMP] = timestamp
        namenode_dic[PLUGIN] = 'yarn'
        namenode_dic[ACTUALPLUGINTYPE] = 'yarn'
        namenode_dic[PLUGINTYPE] = doc_type

    def collect_data(self):
        """Collects all data."""
        if self.is_config_updated:
            data = run_application(0)
        docs = [{"NumRebootedNMs": 0, "_documentType": "yarnStatsClusterMetrics", "NumDecommissionedNMs": 0, "name": "Hadoop:service=ResourceManager,name=ClusterMetrics", "AMLaunchDelayNumOps": 0, "_tag_context": "yarn", "AMRegisterDelayNumOps": 0, "_tag_clustermetrics": "ResourceManager", "modelerType": "ClusterMetrics", "NumLostNMs": 0, "time": 1543301379, "_tag_appName": "hadoopapp1", "NumUnhealthyNMs": 0, "AMRegisterDelayAvgTime": 0, "NumActiveNMs": 0, "AMLaunchDelayAvgTime": 0}]
        for doc in docs:
            self.add_common_params(doc, doc['_documentType'])
            write_json.write(doc)

    def read(self):
        self.collect_data()

    def read_temp(self):
        """
        Collectd first calls register_read. At that time default interval is taken,
        hence temporary function is made to call, the read callback is unregistered
        and read() is called again with interval obtained from conf by register_config callback.
        """
        collectd.unregister_read(self.read_temp) # pylint: disable=E1101
        collectd.register_read(self.read, interval=int(self.interval)) # pylint: disable=E1101

namenodeinstance = YarnStats()
collectd.register_config(namenodeinstance.read_config) # pylint: disable=E1101
collectd.register_read(namenodeinstance.read_temp) # pylint: disable=E1101

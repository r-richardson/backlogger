#!/usr/bin/env python3
import argparse
import os
import sys
import json
from statistics import mean
from datetime import datetime, timedelta
from inspect import getmembers, isfunction
import calendar
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlparse
import yaml
import re
import shutil


# Icons used for PASS or FAIL in the md file
# We use a function or dict that depends on theme? 
# Or just separate dicts.
result_icons_modern = {"pass": "<i class='bi bi-check-circle-fill status-pass'></i>", "fail": "<i class='bi bi-x-circle-fill status-fail'></i>"}
result_icons_legacy = {"pass": "&#x1F49A;", "fail": "&#x1F534;"}
result_icons = result_icons_modern # Default to modern, will be swapped in main if needed

reminder_text_common = "This ticket was set to **{priority}** priority but was not updated [within the SLO period]({url})."
reminder_text = "Please consider picking up this ticket or just set the ticket to the next lower priority."
update_slo_text = "The ticket will be set to the next lower priority **{priority}**."
reminder_regex = (
    r"^This ticket was set to .* priority but was not updated.* Please consider"
)

present = datetime.now()
slo_priorities = {
    "Immediate": {"period": timedelta(days=1),
                  "next_priority": {"id": 6, "name": "Urgent"}}, #or <1 day for all subprojects of qa
    "Urgent": {"period": timedelta(weeks=1),
               "next_priority": {"id": 5, "name": "High"}}, #or <1 day for all subprojects of qa
    "High": {"period": timedelta(days=calendar.monthrange(present.year, present.month)[1]),
             "next_priority": {"id": 4, "name": "Normal"}},
    "Normal": {"period": timedelta(days=sum([calendar.monthrange(present.year, m)[1] for m in range(1,13)])),
               "next_priority": {"id": 3, "name": "Low"}}}


def setup_theme(data):
    """
    Copies the appropriate head.html and foot.html based on the theme.
    """
    theme = data.get('theme', 'modern')
    base_dir = os.path.dirname(os.path.abspath(__file__))
    theme_dir = os.path.join(base_dir, 'themes', theme)
    
    # Fallback to modern if theme not found
    if not os.path.exists(theme_dir):
        print(f"Warning: Theme '{theme}' not found. Falling back to 'modern'.")
        theme_dir = os.path.join(base_dir, 'themes', 'modern')

    shutil.copy(os.path.join(theme_dir, 'head.html'), os.path.join(base_dir, 'head.html'))
    shutil.copy(os.path.join(theme_dir, 'foot.html'), os.path.join(base_dir, 'foot.html'))
    return theme


def retry_request(method, url, data, headers, attempts=7):
    retries = Retry(
        total=attempts, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504]
    )
    http = requests.Session()
    parsed_url = urlparse(url)
    http.mount("{}://".format(parsed_url.scheme), HTTPAdapter(max_retries=retries))
    return http.request(method, url, data=data, headers=headers)


def json_rest(method, url, rest=None):
    text = json.dumps(rest)
    try:
        key = os.environ["REDMINE_API_KEY"]
    except KeyError:
        exit("REDMINE_API_KEY is required to be set")
    headers = {
        "User-Agent": "backlogger ({})".format(data["url"]),
        "Content-Type": "application/json",
        "X-Redmine-API-Key": key,
    }
    r = retry_request(method, url, data=text, headers=headers)
    r.raise_for_status()
    return r.json() if r.text else None


def issue_reminder(conf, poo, poo_reminder_state):
    priority = poo["priority"]["name"]
    msg = " ".join([reminder_text_common.format(priority=priority, url=data["url"]), reminder_text])
    if "comment" in conf:
        msg = conf["comment"]
    if data["reminder-comment-on-issues"]:
        journals = retrieve_journals(poo)
        if journals is None:
            sys.stderr.write(
                "API for {} returned None, skipping reminder".format(poo["id"]))
            return
        elif reminder_exists(poo, journals, poo_reminder_state):
            print("Skipping reminder for {}: a similar reminder already exists".format(poo["id"]))
            if priority == "Low" and poo_reminder_state['has_repeat_reminder']:
                print("Skipping priority update for {}, already at lowest".format(poo["id"]))
                return
            _update_issue_priority(poo["id"], priority, poo_reminder_state, msg)
            return
        _send_first_reminder(poo["id"], msg)


def _send_first_reminder(poo_id, msg):
    print("Writing reminder for {}".format(poo_id))
    url = "{}/{}.json".format(data["web"], poo_id)
    json_rest("PUT", url, {"issue": {"notes": msg}})


def _update_issue_priority(poo_id, priority_current, poo_reminder_state, msg):
    if poo_reminder_state['has_repeat_reminder'] and (poo_reminder_state['last_reminder'] + slo_priorities[priority_current]["period"]) < present:
        note = "No response to reminder. Reducing priority from {} to next lower {} for {}"
        print(note.format(priority_current,
                         slo_priorities[priority_current]["next_priority"]["name"],
                         poo_id))
        url = "{}/{}.json".format(data["web"], poo_id)
        msg = " ".join([reminder_text_common.format(priority=priority_current, url=data["url"]), update_slo_text.format(
            priority=slo_priorities[priority_current]["next_priority"]["name"])])
        json_rest("PUT", url,
                  {"issue":
                   {"priority_id": slo_priorities[priority_current]["next_priority"]["id"],
                    "notes": msg}})


def list_issues(conf, root):
    try:
        for poo in root["issues"]:
            poo_reminder_state = {'last_reminder': datetime.min,
                                  'has_repeat_reminder': False}
            if "updated_on" in conf["query"]:
                issue_reminder(conf, poo, poo_reminder_state)
    except KeyError:
        print("There was an error retrieving the issues " + conf["title"])
    else:
        return int(root["total_count"])


def retrieve_journals(poo):
    url = "{}/{}.json?include=journals".format(data["web"], poo["id"])
    root = json_rest("GET", url)
    if root is None:
        return None
    if "journals" in root["issue"]:
        return root["issue"]["journals"]
    return None


def reminder_exists(poo, journals, state):
    for journal in journals:
        if journal.get("notes", None) is None or len(journal["notes"]) == 0:
            continue
        if re.search(reminder_regex, journal["notes"]):
            state['last_reminder'] = datetime.strptime(journal["created_on"],
                                                       "%Y-%m-%dT%H:%M:%SZ")
            state['has_repeat_reminder'] = True
            return True
    state['has_repeat_reminder'] = False
    return False


def failure_more(conf):
    print(conf["title"] + " has more than " + str(conf["max"]) + " tickets!")
    return False


def check_backlog(conf):
    root = json_rest("GET", data["api"] + "?" + conf["query"])
    issue_count = list_issues(conf, root)
    good = True
    if "max" in conf:
        good = not (
            issue_count > conf["max"] or "min" in conf and issue_count < conf["min"]
        )
    return (good, issue_count)


def collect_results(data, theme):
    all_good = True
    bad_queries = {}
    
    results = []
    
    for conf in data["queries"]:
        good, issue_count = check_backlog(conf)
        url = data["web"] + "?" + conf["query"]
        limits = "<" + str(conf["max"] + 1) if "max" in conf else ""
        if "min" in conf:
            limits += ", >" + str(conf["min"] - 1)
        
        status_icon = result_icons["pass"] if good else result_icons["fail"]
        
        res = {
            "title": conf["title"],
            "url": url,
            "issue_count": issue_count,
            "limits": limits,
            "good": good,
            "status_icon": status_icon
        }
        results.append(res)

        if not good:
            all_good = False
            bad_queries[conf['title']] = {"url": url, "issue_count": issue_count, "limits": limits}
            
    return all_good, results, bad_queries


def generate_markdown(data, results, theme):
    with open("index.md", "w") as md:
        # Header
        if theme == 'legacy':
            md.write("# Backlog Status\n\n")
            md.write(
                "This is the dashboard for [{}]({}).\n".format(data["team"], data["url"])
            )
            md.write(
                "**Latest Run:** " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " UTC\n"
            )
            md.write("*(Please refresh to see latest results)*\n\n")
        else:
            # Modern Theme - Simplified Header
            md.write(f"### [{data['team']}]({data['url']}) Dashboard\n\n")

        # Helper to render table
        def write_table(rows_to_render):
            if not rows_to_render:
                return

            if theme == 'legacy':
                md.write(
                    "Backlog Query | Number of Issues | Limits | Status\n--- | --- | --- | ---\n"
                )
                for res in rows_to_render:
                    md.write(f"[{res['title']}]({res['url']})|{res['issue_count']}|{res['limits']}|{res['status_icon']}\n")
                md.write("\n")
            else:
                md.write('<div class="table-responsive">\n')
                md.write('<table class="table table-hover">\n')
                md.write('<thead><tr><th>Backlog Query</th><th>Number of Issues</th><th>Limits</th><th>Status</th></tr></thead>\n')
                md.write('<tbody>\n')
                for res in rows_to_render:
                    md.write(f"<tr><td><a href='{res['url']}'>{res['title']}</a></td><td>{res['issue_count']}</td><td>{res['limits']}</td><td>{res['status_icon']}</td></tr>\n")
                md.write('</tbody></table></div>\n\n')

        if theme == 'legacy':
            # Just one table
            write_table(results)
        else:
            # Split Tables
            failing = [r for r in results if not r['good']]
            passing = [r for r in results if r['good']]
            
            # 1. Failing Table (if any)
            if failing:
                md.write("#### \u26A0\uFE0F Attention Required\n") # Warning sign
                write_table(failing)
            
            # 2. Passing Table (if any)
            if passing:
                md.write("#### \u2705 Passing Checks\n") # Check mark
                write_table(passing)


def remove_project_part_from_url(url):
    return(re.sub("projects/.*/", "", url))


def cycle_time(issue, status_ids):
    start = datetime.strptime(issue["created_on"], "%Y-%m-%dT%H:%M:%SZ")
    cycle_time = 0
    in_cycle_status = [str(status_ids["In Progress"]), str(status_ids["Feedback"])]
    url = "{}/{}.json?include=journals".format(remove_project_part_from_url(data["web"]), issue["id"])
    issue = json_rest("GET", url)["issue"]
    for journal in issue["journals"]:
        for detail in journal["details"]:
            if detail["name"] == "status_id":
                if detail["new_value"] in in_cycle_status:
                    start = datetime.strptime(
                        journal["created_on"], "%Y-%m-%dT%H:%M:%SZ"
                    )
                elif detail["old_value"] in in_cycle_status:
                    end = datetime.strptime(journal["created_on"], "%Y-%m-%dT%H:%M:%SZ")
                    cycle_time += (end - start).total_seconds()
    return cycle_time


def _today_nanoseconds():
    dt = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    epoch = datetime.utcfromtimestamp(0)
    return int((dt - epoch).total_seconds() * 1000000000)


def render_influxdb(data):
    output = []

    statuses = json_rest("GET", remove_project_part_from_url(data["api"]).replace("issues", "issue_statuses"))
    status_ids = {}
    for status in statuses["issue_statuses"]:
        status_ids[status["name"]] = status["id"]

    for conf in data["queries"]:
        root = json_rest("GET", data["api"] + "?" + conf["query"] + "&limit=100")
        issue_count = list_issues(conf, root)
        status_names = []
        result = {}
        for issue in root["issues"]:
            status = issue["status"]["name"]
            if status not in status_names:
                status_names.append(status)
                result[status] = {"leadTime": [], "cycleTime": []}

            start = datetime.strptime(issue["created_on"], "%Y-%m-%dT%H:%M:%SZ")
            end = datetime.strptime(issue["updated_on"], "%Y-%m-%dT%H:%M:%SZ")
            result[status]["leadTime"].append((end - start).total_seconds())
            if status == "Resolved":
                result[status]["cycleTime"].append(cycle_time(issue, status_ids))
        for status in status_names:
            times = result[status]
            count = len(times["leadTime"])
            if status == "Resolved":
                measure = "leadTime"
                extra = ",leadTime={leadTime},cycleTime={cycleTime},leadTimeSum={leadTimeSum},cycleTimeSum={cycleTimeSum}".format(
                    leadTime=escape_telegraf_str(mean(times["leadTime"]) / 3600, "field value"),
                    cycleTime=escape_telegraf_str(mean(times["cycleTime"]) / 3600, "field value"),
                    leadTimeSum=escape_telegraf_str(sum(times["leadTime"]) / 3600, "field value"),
                    cycleTimeSum=escape_telegraf_str(sum(times["cycleTime"]) / 3600, "field value"),
                )
            else:
                measure = "slo"
                extra = ""
            output.append(
                '{measure},team="{team}",status="{status}",title="{title}" count={count}{extra}'.format(
                    measure=escape_telegraf_str(measure, "measurement"),
                    team=escape_telegraf_str(data["team"], "tag value"),
                    status=escape_telegraf_str(status, "tag value"),
                    title=escape_telegraf_str(conf["title"], "tag value"),
                    count=escape_telegraf_str(count, "field value"),
                    extra=extra,
                )
            )
            if status == "Resolved":
                output[-1] += " " + str(_today_nanoseconds())
    return output

def escape_telegraf_str(value_to_escape, element):
    # See https://docs.influxdata.com/influxdb/cloud/reference/syntax/line-protocol/#special-characters for escaping rules and where they apply
    escaped_str = str(value_to_escape) #especially for field values it can happen that we get an int
    if (element == "field value"): #field values are the only thing where unique rules apply
        escaped_str = escaped_str.replace("\\", "\\\\")
        escaped_str = escaped_str.replace("\"", "\\\"")
        return escaped_str

    # common rules applicable to everything else
    escaped_str = escaped_str.replace(",", "\\,")
    escaped_str = escaped_str.replace(" ", "\\ ")
    if (element != "measurement"):
        escaped_str = escaped_str.replace("=", "\\=")
    return escaped_str

def get_state():
    if os.environ.get('STATE_FOLDER'):
        old_state_file = os.path.join(os.environ['STATE_FOLDER'], "state.json")
        if os.path.exists(old_state_file):
            # open state.json from last run, see if anything changed and send slack notification if needed
            with open(old_state_file, "r") as sj:
                return json.load(sj)

def update_state(bad_queries):
    with open("state.json", "w") as sj:
        state = {
            "bad_queries": bad_queries,
            "updated": datetime.now().isoformat()
        }
        json.dump(state, sj)

def trigger_webhook(state, bad_queries):
    if state:
        old_bad_queries = set(state["bad_queries"].keys())
        new_bad_queries = set(bad_queries.keys())
        fixed_queries = old_bad_queries - new_bad_queries
        broken_queries = new_bad_queries - old_bad_queries
        msg = None
        if broken_queries:
            # something new broke
            msg = f":red_circle: Some queries are exceeding limits:"
            for query in new_bad_queries:
                qd = bad_queries[query]
                msg += f"\n• {query} (Issue count {qd['issue_count']} exceeding limit of [{qd['limits']}])"
        elif fixed_queries and not new_bad_queries:
            # this is the first green run so let's let everyone know
            msg = f":green_heart: All queries within limits again!"
        if msg and os.environ.get('WEBHOOK_URL'):
            r = requests.post(os.environ['WEBHOOK_URL'], json={"msg": msg})

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config", default="queries.yaml", nargs="?")
    parser.add_argument(
        "--output", choices=["markdown", "influxdb"], default="markdown"
    )
    parser.add_argument("--reminder-comment-on-issues", action="store_true")
    parser.add_argument("--exit-code", action="store_true")
    switches = parser.parse_args()
    try:
        all_good = True
        with open(switches.config, "r") as config:
            data = yaml.safe_load(config)
            data["reminder-comment-on-issues"] = switches.reminder_comment_on_issues
            
            # Setup Theme
            theme = setup_theme(data)
            if theme == 'legacy':
                result_icons = result_icons_legacy
            
            if switches.output == "influxdb":
                print("\n".join(line for line in render_influxdb(data)))
            else:
                # Generate
                all_good, results, bad_queries = collect_results(data, theme)
                generate_markdown(data, results, theme)
                        
                # open state.json from last run, see if anything changed and send webhook notification if needed
                state = get_state()
                trigger_webhook(state, bad_queries)
                update_state(bad_queries)
    except FileNotFoundError:
        sys.exit("Configuration file {} not found".format(switches.config))
    if switches.exit_code and not all_good:
        sys.exit(3)
from datetime import datetime, timedelta
import re
import requests
from pydantic import BaseModel
from typing import Optional
import psycopg2
import os

YOUTRACK_URL = "https://stacqan.youtrack.cloud"
API_TOKEN = ""


class Issue(BaseModel):
    issue_id: str
    summary: str
    description: Optional[str]
    project: str
    reporter: str
    status: Optional[str]
    start_date: Optional[datetime]
    estimation: Optional[timedelta]
    time_spent: Optional[timedelta]


def get_custom_field_value(custom_fields, field_name):
    for field in custom_fields:
        if field['name'] == field_name:
            value = field['value']
            if isinstance(value, dict):
                return value.get('presentation') or value.get('name')
            return value
    return None


def parse_time_string(time_string):
    if not time_string:
        return timedelta()

    time_mapping = {'н': 'weeks', 'д': 'days', 'ч': 'hours', 'м': 'minutes'}
    time_pattern = re.compile(r'(\d+)([ндчм])')
    time_kwargs = {}

    for amount, unit in time_pattern.findall(time_string):
        if unit in time_mapping:
            time_kwargs[time_mapping[unit]] = int(amount)

    return timedelta(**time_kwargs)


def get_issues():
    headers = {
        'Authorization': f'Bearer {API_TOKEN}',
        'Accept': 'application/json'
    }

    query = ''

    fields = 'idReadable,summary,description,project(name),reporter(fullName),customFields(name,value(name,presentation)),created,updated'

    url = f'{YOUTRACK_URL}/api/issues?query={query}&fields={fields}'

    start = 0
    batch_size = 100  # YouTrack API might limit the number of issues returned in a single request
    all_issues = []

    while True:
        paginated_url = f'{url}&$skip={start}&$top={batch_size}'
        response = requests.get(paginated_url, headers=headers)

        if response.status_code == 200:
            issues = response.json()
            if not issues:
                break
            for issue in issues:
                issue_id = issue.get('idReadable', 'N/A')
                summary = issue.get('summary', 'N/A')
                description = issue.get('description', 'N/A')
                project = issue.get('project', {}).get('name', 'N/A')
                reporter = issue.get('reporter', {}).get('fullName', 'N/A')

                status = get_custom_field_value(issue.get('customFields', []), 'Статус DEV')
                estimation = get_custom_field_value(issue.get('customFields', []), 'Оценка')
                time_spent = get_custom_field_value(issue.get('customFields', []), 'Реально затраченное время')

                start_date_timestamp = get_custom_field_value(issue.get('customFields', []), 'Дата начала')
                start_date = datetime.fromtimestamp(int(start_date_timestamp) / 1000) if isinstance(
                    start_date_timestamp,
                    int) else None

                # Parse estimation and time spent, calculate the difference
                estimation_timedelta = parse_time_string(estimation)
                time_spent_timedelta = parse_time_string(time_spent)

                all_issues.append(
                    Issue(
                        issue_id=issue_id,
                        summary=summary,
                        description=description,
                        project=project,
                        reporter=reporter,
                        status=status,
                        start_date=start_date,
                        estimation=estimation_timedelta,
                        time_spent=time_spent_timedelta
                    )
                )
            start += batch_size
        else:
            print(f"Failed to fetch issues: {response.status_code}")
            print(response.text)
            break

    print(f"Total issues fetched: {len(all_issues)}")
    return all_issues


def make_migrations(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id SERIAL PRIMARY KEY,
            issue_id VARCHAR(50) NOT NULL,
            summary VARCHAR(255) NOT NULL,
            description TEXT,
            project VARCHAR(50) NOT NULL,
            reporter VARCHAR(50) NOT NULL,
            status VARCHAR(50),
            start_date TIMESTAMP,
            estimation INTERVAL,
            time_spent INTERVAL,
            CONSTRAINT unique_issue_id UNIQUE (issue_id)
        );
        """)

    conn.commit()
    cur.close()


def insert_into_postgres(conn, issues):
    cur = conn.cursor()

    upsert_query = """
        INSERT INTO issues (issue_id, summary, description, project, reporter, status, start_date, estimation, time_spent)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (issue_id) DO UPDATE SET
            summary = EXCLUDED.summary,
            description = EXCLUDED.description,
            project = EXCLUDED.project,
            reporter = EXCLUDED.reporter,
            status = EXCLUDED.status,
            start_date = EXCLUDED.start_date,
            estimation = EXCLUDED.estimation,
            time_spent = EXCLUDED.time_spent
    """

    for issue in issues:
        data_tuple = (
            issue.issue_id,
            issue.summary,
            issue.description,
            issue.project,
            issue.reporter,
            issue.status,
            issue.start_date,
            issue.estimation,
            issue.time_spent
        )

        cur.execute(upsert_query, data_tuple)

    conn.commit()
    cur.close()

def main():
    print("Connecting to Postgres...")
    conn = psycopg2.connect(user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"],
                            database=os.environ["POSTGRES_DATABASE"], host=os.environ["POSTGRES_HOST"],
                            port=os.environ["POSTGRES_PORT"])

    print("Running migrations...")
    make_migrations(conn)

    print(f"Getting issues...")
    issues = get_issues()

    insert_into_postgres(conn, issues)

    conn.close()


if __name__ == "__main__":
    main()


import csv
import io
import re
from datetime import datetime

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


# ============================================================
# GOOGLE SHEET
# ============================================================

SPREADSHEET_ID = "1FIbRKZURuq11JXmkp0w-erTdroI2fWCoZhmxjpze-uE"


# Replace these GIDs with the actual GID of each section tab.
SECTION_GIDS = {
    "313-AIAGAI-1D": "GID_FOR_SECTION_1",
    "106-AIDE-1A": "GID_FOR_SECTION_2",
    "109-AIDE-1B": "GID_FOR_SECTION_3",
}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="ERP Attendance Automation"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ============================================================
# HELPERS
# ============================================================

def clean(value):
    return str(value or "").strip()


def normalize_reg(value):
    return re.sub(
        r"\s+",
        "",
        clean(value).upper()
    )


def normalize_date(value):
    value = clean(value)

    formats = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%Y-%m-%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value,
                fmt
            ).strftime("%d-%m-%Y")
        except ValueError:
            pass

    return value


def normalize_status(value):

    value = clean(value).lower()

    if value in {
        "present",
        "p",
        "yes",
        "1"
    }:
        return "Present"

    if value in {
        "absent",
        "a",
        "no",
        "0"
    }:
        return "Absent"

    return None


# ============================================================
# READ GOOGLE SHEET
# ============================================================

def read_sheet(section):

    if section not in SECTION_GIDS:
        raise ValueError(
            f"Unknown section: {section}"
        )

    gid = SECTION_GIDS[section]

    url = (
        f"https://docs.google.com/spreadsheets/d/"
        f"{SPREADSHEET_ID}/export"
        f"?format=csv&gid={gid}"
    )

    response = requests.get(
        url,
        timeout=30
    )

    if response.status_code != 200:
        raise ValueError(
            f"Google Sheets returned HTTP "
            f"{response.status_code}"
        )

    text = response.text

    if not text.strip():
        raise ValueError(
            "Google Sheet returned empty data."
        )

    return list(
        csv.reader(
            io.StringIO(text)
        )
    )


# ============================================================
# FIND REGISTRATION COLUMN
# ============================================================

def find_reg_column(headers):

    possible = {
        "registration no",
        "registration number",
        "reg no",
        "regno",
        "roll no",
        "roll number",
        "registration",
    }

    for index, header in enumerate(headers):

        normalized = (
            clean(header)
            .lower()
        )

        if normalized in possible:
            return index

    # Fallback search
    for index, header in enumerate(headers):

        normalized = (
            clean(header)
            .lower()
        )

        if (
            "registration" in normalized
            or "reg no" in normalized
            or "roll" in normalized
        ):
            return index

    return None


# ============================================================
# FIND DATE COLUMN
# ============================================================

def find_date_column(headers, requested_date):

    requested_date = normalize_date(
        requested_date
    )

    for index, header in enumerate(headers):

        if normalize_date(header) == requested_date:
            return index

    return None


# ============================================================
# ATTENDANCE ENDPOINT
# ============================================================

@app.get("/attendance")
def attendance(
    section: str,
    date: str
):

    try:

        rows = read_sheet(section)

        if not rows:
            raise ValueError(
                "No rows found."
            )

        headers = rows[0]

        reg_column = find_reg_column(
            headers
        )

        if reg_column is None:
            raise ValueError(
                "Registration-number column "
                "was not found."
            )

        date_column = find_date_column(
            headers,
            date
        )

        if date_column is None:
            raise ValueError(
                f"Date '{date}' was not found "
                f"in the header row."
            )

        students = {}

        for row in rows[1:]:

            if reg_column >= len(row):
                continue

            registration = normalize_reg(
                row[reg_column]
            )

            if not registration:
                continue

            if date_column >= len(row):
                continue

            status = normalize_status(
                row[date_column]
            )

            if status:
                students[
                    registration
                ] = status

        return {
            "section": section,
            "date": normalize_date(date),
            "students": students,
            "student_count": len(students)
        }

    except Exception as error:

        print(
            "ERROR:",
            repr(error)
        )

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


# ============================================================
# HEALTH
# ============================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": "ERP Attendance Automation"
    }


@app.get("/health")
def health():

    return {
        "status": "healthy"
    }


@app.get("/sections")
def sections():

    return {
        "sections": list(
            SECTION_GIDS.keys()
        )
    }

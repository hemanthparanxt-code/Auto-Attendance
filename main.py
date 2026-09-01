
import os
import json
import re
from datetime import datetime
from typing import Dict, Any

import gspread
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2.service_account import Credentials


# ============================================================
# CONFIGURATION
# ============================================================

SPREADSHEET_ID = "1FIbRKZURuq11JXmkp0w-erTdroI2fWCoZhmxjpze-uE"

# These MUST exactly match the Google Sheet tab names.
SECTION_SHEETS = {
    "313-AIAGAI-1D": "313-AIAGAI-1D",
    "106-AIDE-1A": "106-AIDE-1A",
    "109-AIDE-1B": "109-AIDE-1B",
}

ALLOWED_STATUSES = {
    "present": "Present",
    "p": "Present",
    "yes": "Present",
    "1": "Present",

    "absent": "Absent",
    "a": "Absent",
    "no": "Absent",
    "0": "Absent",
}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="ERP Attendance Automation API",
    version="1.0.0",
)


# IMPORTANT:
# The ERP page is on a different origin, so the browser needs CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ============================================================
# GOOGLE SHEETS AUTHENTICATION
# ============================================================

def get_google_client():
    """
    Reads the Google service-account JSON from the
    GOOGLE_SERVICE_ACCOUNT_JSON environment variable.
    """

    raw_credentials = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not raw_credentials:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON environment variable is missing."
        )

    try:
        credentials_info = json.loads(raw_credentials)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON."
        ) from exc

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ]

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=scopes,
    )

    return gspread.authorize(credentials)


# ============================================================
# HELPERS
# ============================================================

def normalize(value: Any) -> str:
    """
    Normalize text for reliable comparison.
    """

    if value is None:
        return ""

    value = str(value)

    # Remove spaces, line breaks and invisible characters.
    value = value.replace("\u200b", "")
    value = value.replace("\xa0", " ")

    return value.strip()


def normalize_reg_no(value: Any) -> str:
    """
    Normalize registration numbers.

    Example:
        252U1R8045
        252U1R8045
        252U1R8045
    """

    value = normalize(value)

    return re.sub(r"\s+", "", value).upper()


def normalize_date(value: Any) -> str:
    """
    Convert different date formats to DD-MM-YYYY.

    Supported examples:

        01-09-2026
        01/09/2026
        01.09.2026
        2026-09-01
    """

    value = normalize(value)

    formats = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%d-%m-%y",
        "%d/%m/%y",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%d-%m-%Y")
        except ValueError:
            pass

    return value


def find_date_column(headers, requested_date):
    """
    Find the column containing the requested date.
    """

    requested_date = normalize_date(requested_date)

    for index, header in enumerate(headers):
        header_normalized = normalize_date(header)

        if header_normalized == requested_date:
            return index

    return None


def find_registration_column(headers):
    """
    Find the registration-number column.

    We support several common header names.
    """

    possible_names = {
        "registration number",
        "registration no",
        "reg no",
        "reg. no",
        "regno",
        "registration",
        "roll no",
        "roll number",
        "student id",
        "id",
    }

    for index, header in enumerate(headers):

        normalized_header = (
            normalize(header)
            .lower()
            .replace("_", " ")
        )

        if normalized_header in possible_names:
            return index

    # Fallback:
    # Search for headers containing registration / reg / roll.
    for index, header in enumerate(headers):

        normalized_header = (
            normalize(header)
            .lower()
        )

        if (
            "registration" in normalized_header
            or "reg no" in normalized_header
            or "roll" in normalized_header
        ):
            return index

    return None


def convert_status(value):
    """
    Convert sheet value into Present / Absent.

    Unknown values return None.
    """

    value = normalize(value).lower()

    if not value:
        return None

    return ALLOWED_STATUSES.get(value)


# ============================================================
# READ ATTENDANCE
# ============================================================

def read_attendance(section: str, requested_date: str):
    """
    Read one section and one date from Google Sheets.
    """

    if section not in SECTION_SHEETS:
        raise ValueError(
            f"Unknown section: {section}"
        )

    sheet_name = SECTION_SHEETS[section]

    client = get_google_client()

    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        raise ValueError(
            f"Google Sheet tab '{sheet_name}' was not found."
        )

    values = worksheet.get_all_values()

    if not values:
        raise ValueError(
            f"Sheet '{sheet_name}' is empty."
        )

    headers = values[0]

    # --------------------------------------------------------
    # Find registration-number column
    # --------------------------------------------------------

    reg_column = find_registration_column(headers)

    if reg_column is None:
        raise ValueError(
            "Could not find registration-number column. "
            "Expected something like 'Registration No'."
        )

    # --------------------------------------------------------
    # Find date column
    # --------------------------------------------------------

    date_column = find_date_column(
        headers,
        requested_date
    )

    if date_column is None:

        available_dates = [
            normalize(header)
            for header in headers
            if normalize(header)
        ]

        raise ValueError(
            f"Date '{requested_date}' was not found. "
            f"Available headers: {available_dates}"
        )

    # --------------------------------------------------------
    # Build attendance dictionary
    # --------------------------------------------------------

    students: Dict[str, str] = {}

    ignored_rows = 0
    invalid_status_rows = 0

    for row in values[1:]:

        if reg_column >= len(row):
            ignored_rows += 1
            continue

        reg_no = normalize_reg_no(
            row[reg_column]
        )

        if not reg_no:
            ignored_rows += 1
            continue

        if date_column >= len(row):
            ignored_rows += 1
            continue

        raw_status = row[date_column]

        status = convert_status(raw_status)

        if status is None:
            invalid_status_rows += 1
            continue

        students[reg_no] = status

    return {
        "section": section,
        "sheet": sheet_name,
        "date": normalize_date(requested_date),
        "students": students,
        "student_count": len(students),
        "ignored_rows": ignored_rows,
        "invalid_status_rows": invalid_status_rows,
    }


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": "ERP Attendance Automation API",
        "version": "1.0.0",
    }


@app.get("/health")
def health():

    return {
        "status": "healthy"
    }


@app.get("/sections")
def sections():

    return {
        "sections": list(SECTION_SHEETS.keys())
    }


@app.get("/attendance")
def attendance(
    section: str = Query(...),
    date: str = Query(...),
):

    try:

        result = read_attendance(
            section=section,
            requested_date=date,
        )

        return result

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:

        print("ERROR:", repr(exc))

        raise HTTPException(
            status_code=500,
            detail="Unable to read Google Sheet.",
        )



import os
import re
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


# ============================================================
# CONFIGURATION
# ============================================================

SPREADSHEET_ID = "1FIbRKZURuq11JXmkp0w-erTdroI2fWCoZhmxjpze-uE"

# IMPORTANT:
# The values on the RIGHT must exactly match your Google
# Sheets tab names.
#
# Example:
# "106-AIDE-1A": "106-AIDE-1A"
#
# If your actual tab names are different, change them here.

SECTION_SHEETS = {
    "313-AIAGAI-1D": "313-AIAGAI-1D",
    "106-AIDE-1A": "106-AIDE-1A",
    "109-AIDE-1B": "109-AIDE-1B",
}


# Render Secret File
GOOGLE_CREDENTIAL_FILE = (
    "/etc/secrets/google-service-account.json"
)


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="ERP Attendance Automation API",
    version="1.0.0",
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean(value):
    """
    Convert a value to a clean string.
    """
    if value is None:
        return ""

    return str(value).strip()


def normalize_registration(value):
    """
    Normalize registration number.

    Example:
        '252U1R8045'
        ' 252U1R8045 '
        '252 U1R 8045'

    all become:

        '252U1R8045'
    """

    return re.sub(
        r"\s+",
        "",
        clean(value).upper(),
    )


def normalize_status(value):
    """
    Convert common attendance values into
    Present / Absent.
    """

    value = clean(value).lower()

    present_values = {
        "present",
        "p",
        "yes",
        "y",
        "1",
        "true",
    }

    absent_values = {
        "absent",
        "a",
        "no",
        "n",
        "0",
        "false",
    }

    if value in present_values:
        return "Present"

    if value in absent_values:
        return "Absent"

    return None


def normalize_date(value):
    """
    Convert supported date formats to:

        DD-MM-YYYY
    """

    value = clean(value)

    if not value:
        return ""

    formats = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%m-%d-%Y",
        "%m/%d/%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value,
                fmt,
            ).strftime("%d-%m-%Y")
        except ValueError:
            continue

    return value


# ============================================================
# GOOGLE AUTHENTICATION
# ============================================================

def get_google_client():
    """
    Authenticate using the Google service-account
    JSON stored in Render Secret Files.
    """

    if not os.path.exists(
        GOOGLE_CREDENTIAL_FILE
    ):
        raise RuntimeError(
            "Google service-account file not found at: "
            + GOOGLE_CREDENTIAL_FILE
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    try:

        credentials = (
            Credentials.from_service_account_file(
                GOOGLE_CREDENTIAL_FILE,
                scopes=scopes,
            )
        )

        client = gspread.authorize(
            credentials
        )

        return client

    except Exception as error:

        raise RuntimeError(
            "Google authentication failed: "
            + str(error)
        )


# ============================================================
# OPEN SPREADSHEET
# ============================================================

def get_spreadsheet():

    try:

        client = get_google_client()

        spreadsheet = client.open_by_key(
            SPREADSHEET_ID
        )

        return spreadsheet

    except Exception as error:

        raise RuntimeError(
            "Could not open Google Spreadsheet: "
            + str(error)
        )


# ============================================================
# OPEN SECTION WORKSHEET
# ============================================================

def get_worksheet(section):

    if section not in SECTION_SHEETS:

        raise ValueError(
            "Unknown section: "
            + section
            + ". Available sections: "
            + ", ".join(
                SECTION_SHEETS.keys()
            )
        )

    worksheet_name = SECTION_SHEETS[
        section
    ]

    try:

        spreadsheet = get_spreadsheet()

        worksheet = spreadsheet.worksheet(
            worksheet_name
        )

        return worksheet

    except gspread.exceptions.WorksheetNotFound:

        raise ValueError(
            f"Google Sheet tab '{worksheet_name}' "
            f"was not found."
        )

    except Exception as error:

        raise RuntimeError(
            f"Could not open worksheet "
            f"'{worksheet_name}': {error}"
        )


# ============================================================
# FIND REGISTRATION COLUMN
# ============================================================

def find_registration_column(headers):

    possible_names = {
        "registration no",
        "registration number",
        "registration",
        "reg no",
        "reg number",
        "regno",
        "roll no",
        "roll number",
        "rollno",
        "admission no",
        "admission number",
    }

    # First: exact matches
    for index, header in enumerate(headers):

        normalized = (
            clean(header)
            .lower()
        )

        if normalized in possible_names:
            return index

    # Second: partial matches
    for index, header in enumerate(headers):

        normalized = (
            clean(header)
            .lower()
        )

        if (
            "registration" in normalized
            or "reg no" in normalized
            or "regno" in normalized
            or "roll no" in normalized
            or "rollno" in normalized
            or "admission no" in normalized
        ):
            return index

    return None


# ============================================================
# FIND DATE COLUMN
# ============================================================

def find_date_column(
    headers,
    requested_date,
):
    """
    Find the column containing the requested date.
    """

    requested = normalize_date(
        requested_date
    )

    # --------------------------------------------------------
    # Direct normalized comparison
    # --------------------------------------------------------

    for index, header in enumerate(headers):

        header_date = normalize_date(
            header
        )

        if header_date == requested:

            return index

    # --------------------------------------------------------
    # Additional comparison
    # --------------------------------------------------------

    requested_digits = re.sub(
        r"\D",
        "",
        requested,
    )

    if requested_digits:

        for index, header in enumerate(headers):

            header_digits = re.sub(
                r"\D",
                "",
                clean(header),
            )

            if (
                header_digits
                == requested_digits
            ):
                return index

    return None


# ============================================================
# READ ATTENDANCE
# ============================================================

def read_attendance(
    section,
    date,
):

    worksheet = get_worksheet(
        section
    )

    try:

        rows = worksheet.get_all_values()

    except Exception as error:

        raise RuntimeError(
            "Could not read Google Sheet data: "
            + str(error)
        )

    if not rows:

        raise ValueError(
            f"Worksheet '{SECTION_SHEETS[section]}' "
            "is empty."
        )

    headers = rows[0]

    if not headers:

        raise ValueError(
            "The first row of the worksheet "
            "is empty."
        )

    # --------------------------------------------------------
    # Find registration column
    # --------------------------------------------------------

    registration_column = (
        find_registration_column(
            headers
        )
    )

    if registration_column is None:

        raise ValueError(
            "Could not find the registration-number "
            "column.\n\n"
            "Headers found:\n"
            + " | ".join(headers)
        )

    # --------------------------------------------------------
    # Find date column
    # --------------------------------------------------------

    date_column = find_date_column(
        headers,
        date,
    )

    if date_column is None:

        raise ValueError(
            f"Date '{date}' was not found "
            "in the first row.\n\n"
            "Headers found:\n"
            + " | ".join(headers)
        )

    # --------------------------------------------------------
    # Build attendance dictionary
    # --------------------------------------------------------

    students = {}

    for row_number, row in enumerate(
        rows[1:],
        start=2,
    ):

        # Registration column doesn't exist
        if (
            registration_column
            >= len(row)
        ):
            continue

        registration = (
            normalize_registration(
                row[
                    registration_column
                ]
            )
        )

        # Empty registration
        if not registration:
            continue

        # Date column doesn't exist
        if (
            date_column
            >= len(row)
        ):
            continue

        raw_status = row[
            date_column
        ]

        status = normalize_status(
            raw_status
        )

        # Ignore blank/unknown statuses
        if status is None:
            continue

        students[
            registration
        ] = status

    return students


# ============================================================
# ROOT ENDPOINT
# ============================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": "ERP Attendance Automation API",
        "version": "1.0.0",
    }


# ============================================================
# HEALTH ENDPOINT
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "healthy",
    }


# ============================================================
# SECTION ENDPOINT
# ============================================================

@app.get("/sections")
def sections():

    return {
        "sections": list(
            SECTION_SHEETS.keys()
        ),
    }


# ============================================================
# GOOGLE SHEET DIAGNOSTIC
# ============================================================

@app.get("/diagnostic")
def diagnostic():

    result = {
        "api": "OK",
        "google_auth": False,
        "spreadsheet": False,
        "worksheets": [],
    }

    try:

        client = get_google_client()

        result[
            "google_auth"
        ] = True

        spreadsheet = client.open_by_key(
            SPREADSHEET_ID
        )

        result[
            "spreadsheet"
        ] = True

        worksheets = (
            spreadsheet.worksheets()
        )

        result[
            "worksheets"
        ] = [
            {
                "title": sheet.title,
                "id": sheet.id,
            }
            for sheet in worksheets
        ]

        return result

    except Exception as error:

        result[
            "error"
        ] = str(error)

        return result


# ============================================================
# ATTENDANCE ENDPOINT
# ============================================================

@app.get("/attendance")
def attendance(
    section: str,
    date: str,
):

    try:

        # ----------------------------------------------------
        # Validate section
        # ----------------------------------------------------

        if section not in SECTION_SHEETS:

            raise ValueError(
                f"Invalid section '{section}'. "
                f"Available sections: "
                f"{', '.join(SECTION_SHEETS.keys())}"
            )

        # ----------------------------------------------------
        # Normalize date
        # ----------------------------------------------------

        normalized_date = normalize_date(
            date
        )

        if not normalized_date:

            raise ValueError(
                "Date cannot be empty."
            )

        # ----------------------------------------------------
        # Read attendance
        # ----------------------------------------------------

        students = read_attendance(
            section,
            normalized_date,
        )

        # ----------------------------------------------------
        # Return response
        # ----------------------------------------------------

        return {
            "status": "success",
            "section": section,
            "date": normalized_date,
            "students": students,
            "student_count": len(
                students
            ),
        }

    except Exception as error:

        print(
            "ATTENDANCE ERROR:",
            repr(error)
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )

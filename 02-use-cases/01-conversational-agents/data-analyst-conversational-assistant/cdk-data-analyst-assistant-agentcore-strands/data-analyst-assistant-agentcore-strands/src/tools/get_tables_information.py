from strands import tool
from src.utils import load_file_content


@tool
def get_tables_information() -> dict:
    """
    Provides information related to the data tables available to generate the SQL queries to answer the users questions

    Returns:
        dict: A dictionary containing the information about the tables
              with keys 'toolUsed' and 'information'

    Note:
        Expects a file named 'tables_information.txt' in the current directory.
        Returns an error message in the dictionary if the file is not found.
    """
    try:
        return {
            "toolUsed": "get_tables_information",
            "information": load_file_content("src/tools/tables_information.txt"),
        }
    except FileNotFoundError:
        return {
            "toolUsed": "get_tables_information",
            "information": "Error: src/tools/tables_information.txt file not found. Please create this file with your tables information.",
        }
    except Exception as e:
        return {
            "toolUsed": "get_tables_information",
            "information": f"Error reading tables information: {str(e)}",
        }

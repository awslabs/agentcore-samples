def load_file_content(file_path: str, default_content: str = None) -> str:
    """
    Load file content with optional fallback and comprehensive error handling.

    Args:
        file_path (str): Path to the file to read
        default_content (str, optional): Fallback content if file not found

    Returns:
        str: File content or default content if provided

    Raises:
        FileNotFoundError: If file not found and no default provided
        Exception: For other file reading errors with detailed message
    """
    try:
        with open(file_path, "r") as file:
            return file.read()
    except FileNotFoundError:
        if default_content is not None:
            return default_content
        else:
            raise FileNotFoundError(f"File not found: {file_path}")
    except Exception as e:
        raise Exception(f"Error reading file {file_path}: {str(e)}")

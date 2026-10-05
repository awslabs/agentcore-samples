def load_file_content(file_path: str, default_content: str | None = None) -> str:
    """
    Load file content with optional fallback and comprehensive error handling.

    Args:
        file_path (str): Path to the file to read
        default_content (str, optional): Fallback content if file not found

    Returns:
        str: File content or default content if provided

    Raises:
        FileNotFoundError: If file not found and no default provided
        OSError: For other file reading errors with detailed message
    """
    try:
        with open(file_path, "r") as file:
            return file.read()
    except FileNotFoundError:
        if default_content is not None:
            return default_content
        raise FileNotFoundError(f"File not found: {file_path}") from None
    except OSError as e:
        raise OSError(f"Error reading file {file_path}: {e!s}") from e

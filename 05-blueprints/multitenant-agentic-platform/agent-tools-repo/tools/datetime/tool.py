from strands import tool
from datetime import datetime
import pytz


@tool
def get_datetime(timezone: str = "UTC") -> str:
    """
    Get the current date, time, and timezone information.

    This tool returns the current date and time in the specified timezone.
    Useful for time-aware responses and scheduling tasks.

    Args:
        timezone: Timezone name (e.g., "UTC", "America/New_York", "Europe/London", "Asia/Tokyo")
                 Default is "UTC". Use standard IANA timezone names.

    Returns:
        Formatted string with current date, time, day of week, and timezone information

    Example:
        result = get_datetime()  # Returns UTC time
        result = get_datetime("America/New_York")  # Returns New York time
        result = get_datetime("Europe/London")  # Returns London time
    """
    try:
        # Get timezone object
        try:
            tz = pytz.timezone(timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            # If invalid timezone, default to UTC and note the error
            tz = pytz.UTC
            timezone_note = f" (Note: '{timezone}' is not a valid timezone, using UTC instead)"
        else:
            timezone_note = ""

        # Get current time in specified timezone
        now = datetime.now(tz)

        # Format the response
        result = []
        result.append(f"**Current Date & Time Information**{timezone_note}")
        result.append("")
        result.append(f"📅 **Date**: {now.strftime('%A, %B %d, %Y')}")
        result.append(f"🕐 **Time**: {now.strftime('%I:%M:%S %p')}")
        result.append(f"⏰ **24-Hour Time**: {now.strftime('%H:%M:%S')}")
        result.append(f"🌍 **Timezone**: {timezone} ({now.strftime('%Z')})")
        result.append(f"📍 **UTC Offset**: {now.strftime('%z')}")
        result.append(f"📊 **ISO Format**: {now.isoformat()}")
        result.append(f"🗓️  **Day of Year**: Day {now.strftime('%j')} of {now.year}")
        result.append(f"📆 **Week Number**: Week {now.strftime('%U')}")

        return "\n".join(result)

    except Exception as e:
        return f"Error getting datetime information: {str(e)}"

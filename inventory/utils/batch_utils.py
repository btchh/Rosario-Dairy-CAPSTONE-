from django.utils import timezone
# Batch Number Generator

def generate_batch_number(prefix, sequence):
    """
    Generate a unique batch number for an ingredient / product based on its name and the current date.
    The format is: PRD-2506-001 / ING-2506-001
    """

    now = timezone.now()
    year = now.strftime("%y")  # Get last two digits of the year
    month = now.strftime("%m")  # Get the month in two digits
    seq = f"{sequence:03d}"  # Format sequence as a three-digit number with leading zeros
    return f"{prefix}-{year}{month}-{seq}" # Return the formatted batch number


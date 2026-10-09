"""Conservative black-frame checks on private, temporary decoded video frames."""


def black_frame(path):
    from PIL import Image
    with Image.open(path) as frame:
        frame.thumbnail((160, 90))
        histogram = frame.convert('L').histogram()
    total = sum(histogram)
    # A channel logo, subtitles or a dark scene with highlights should count as picture.
    return (total > 0 and sum(histogram[:12]) / total >= .999
            and sum(value * count for value, count in enumerate(histogram)) / total < 4)

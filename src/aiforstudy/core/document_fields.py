"""Pure JSON string-field traversal; no author, audit or mutation dependencies."""

def text_fields(document, prefix=''):
    if isinstance(document, dict):
        for key, value in document.items():
            pointer = prefix + '/' + str(key).replace('~', '~0').replace('/', '~1')
            if isinstance(value, str):
                yield pointer, value
            elif isinstance(value, (list, dict)):
                yield from text_fields(value, pointer)
    elif isinstance(document, list):
        for index, value in enumerate(document):
            pointer = prefix + '/' + str(index)
            if isinstance(value, str):
                yield pointer, value
            else:
                yield from text_fields(value, pointer)

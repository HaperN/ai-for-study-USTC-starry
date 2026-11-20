"""Read exact input text references in offline transports, like the prompt directs."""


def expand_prompt_material(value, payload):
    def at(pointer):
        target = payload
        for part in pointer.strip('/').split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            target = target[int(part)] if isinstance(target, list) else target[part]
        return target

    if isinstance(value, dict) and '$source_lines' in value:
        return ''.join(row['text'] for row in at(value['$source_lines']))
    if isinstance(value, dict) and '$text_ref' in value:
        target = expand_prompt_material(at(value['$text_ref']), payload)
        assert isinstance(target, str)
        return target[value.get('start', 0):value.get('end', len(target))]
    if isinstance(value, dict):
        return {k: expand_prompt_material(v, payload) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_prompt_material(v, payload) for v in value]
    return value

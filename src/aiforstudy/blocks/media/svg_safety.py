"""One SVG boundary for execution, worker output and acceptance consumers."""
from pathlib import Path
import base64
from io import BytesIO
import re
import xml.etree.ElementTree as ET
from ...errors import ContractError


def _external_css(value):
    # This boundary accepts the plain CSS emitted by our renderers. Reject
    # escapes/comments instead of partially parsing CSS: escaped identifiers
    # hide url/import, and comment-looking text inside a quoted URL is data.
    if '\\' in value or '/*' in value or '*/' in value:
        return True
    if re.search(r'@import|(?:-webkit-)?image-set\s*\(', value, re.IGNORECASE):
        return True
    return any(not match.group(1).strip().strip('\'"').startswith('#')
               for match in re.finditer(r'url\(([^)]*)\)',value,re.IGNORECASE))


def safe_svg(path):
    return validate_svg_source(Path(path).read_text(encoding='utf-8'))


def validate_svg_source(source):
    if '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper(): raise ContractError('SVG external entities forbidden')
    try: root=ET.fromstring(source)
    except ET.ParseError as exc: raise ContractError('invalid SVG') from exc
    if root.tag.split('}')[-1] != 'svg': raise ContractError('SVG root missing')
    embedded_bytes=0
    for element in root.iter():
        tag=element.tag.split('}')[-1].lower()
        if tag in ('script','foreignobject','iframe','object','embed','animate','set','animatetransform','animatemotion'):
            raise ContractError('active/external SVG content forbidden')
        image_links={key:value for key,value in element.attrib.items() if key.split('}')[-1]=='href'} if tag=='image' else {}
        if tag=='image':
            if not image_links: raise ContractError('SVG image requires bounded embedded PNG/JPEG')
            for value in image_links.values():
                embedded_bytes+=_check_embedded_raster(value)
            if embedded_bytes>16*1024*1024: raise ContractError('SVG embedded raster total exceeds 16 MiB')
        for key,value in element.attrib.items():
            local_key = key.split('}')[-1].lower()
            if (local_key.startswith('on') or key == '{http://www.w3.org/XML/1998/namespace}base'
                    or (local_key == 'href' and not value.startswith('#') and key not in image_links)
                    or _external_css(value)):
                raise ContractError('active/external SVG attribute forbidden')
        if tag=='style' and _external_css(element.text or ''):
            raise ContractError('external SVG style forbidden')
    return source


def _check_embedded_raster(value):
    """Allow Matplotlib's local raster tiles, never active or external images."""
    from PIL import Image
    match=re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/=\t\r\n ]+)',value)
    if not match or len(match[2])>12*1024*1024:
        raise ContractError('SVG image must be bounded base64 PNG/JPEG')
    try:
        raw=base64.b64decode(re.sub(r'[\t\r\n ]','',match[2]),validate=True)
        if len(raw)>8*1024*1024: raise ValueError('embedded raster exceeds 8 MiB')
        with Image.open(BytesIO(raw)) as image:
            if (image.format!={'png':'PNG','jpeg':'JPEG'}[match[1]]
                    or max(image.size)>8192 or image.width*image.height>16_777_216):
                raise ValueError('embedded raster format or dimensions invalid')
            image.verify()
    except (ValueError,OSError,Image.DecompressionBombError) as exc:
        raise ContractError('invalid embedded SVG raster: '+str(exc)) from exc
    return len(raw)

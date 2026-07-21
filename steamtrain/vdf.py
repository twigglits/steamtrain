"""Text KeyValues (VDF) parser/serializer for Steam config files.

Stdlib-only. Parses into ordered nested dicts and serializes back in the
exact style Steam writes (tab indentation, two tabs between key and value),
so an unmodified parse/dump round-trip is byte-identical.
"""

_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}
_REVERSE_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t"}


class VdfError(ValueError):
    pass


def _tokenize(text):
    """Yield tokens: ('str', value) for quoted/bare strings, ('{',) and ('}',)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "/" and text[i : i + 2] == "//":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif c == "{":
            yield ("{", None)
            i += 1
        elif c == "}":
            yield ("}", None)
            i += 1
        elif c == '"':
            i += 1
            parts = []
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    esc = text[i + 1]
                    if esc in _ESCAPES:
                        parts.append(_ESCAPES[esc])
                        i += 2
                        continue
                    parts.append(c)
                    i += 1
                elif c == '"':
                    i += 1
                    break
                else:
                    parts.append(c)
                    i += 1
            else:
                raise VdfError("unterminated quoted string")
            yield ("str", "".join(parts))
        elif c == "[":
            # platform conditional like [$LINUX]; skip it
            end = text.find("]", i)
            if end == -1:
                raise VdfError("unterminated conditional")
            i = end + 1
        else:
            start = i
            while i < n and text[i] not in ' \t\r\n"{}':
                i += 1
            yield ("str", text[start:i])


def loads(text):
    """Parse VDF text into nested dicts (insertion-ordered)."""
    tokens = _tokenize(text)
    root = {}
    stack = [root]
    pending_key = None
    for kind, value in tokens:
        if kind == "str":
            if pending_key is None:
                pending_key = value
            else:
                stack[-1][pending_key] = value
                pending_key = None
        elif kind == "{":
            if pending_key is None:
                raise VdfError("block has no key")
            block = {}
            stack[-1][pending_key] = block
            stack.append(block)
            pending_key = None
        else:  # '}'
            if pending_key is not None:
                raise VdfError(f"dangling key {pending_key!r} before '}}'")
            if len(stack) == 1:
                raise VdfError("unbalanced '}'")
            stack.pop()
    if pending_key is not None:
        raise VdfError(f"dangling key {pending_key!r} at end of input")
    if len(stack) != 1:
        raise VdfError("unclosed block")
    return root


def _escape(s):
    return "".join(_REVERSE_ESCAPES.get(c, c) for c in s)


def _dump_block(data, depth, out):
    indent = "\t" * depth
    for key, value in data.items():
        if isinstance(value, dict):
            out.append(f'{indent}"{_escape(key)}"\n{indent}{{\n')
            _dump_block(value, depth + 1, out)
            out.append(f"{indent}}}\n")
        else:
            out.append(f'{indent}"{_escape(key)}"\t\t"{_escape(str(value))}"\n')


def dumps(data):
    """Serialize nested dicts to VDF text in Steam's own formatting."""
    out = []
    _dump_block(data, 0, out)
    return "".join(out)

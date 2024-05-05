import sys
import ruamel.yaml
from ruamel.yaml import ScalarNode
from ruamel.yaml.constructor import ConstructorError


class MyLiteralScalarString(ruamel.yaml.scalarstring.LiteralScalarString):
    __slots__ = ('comment', 'lc')


class MyFoldedScalarString(ruamel.yaml.scalarstring.FoldedScalarString):
    __slots__ = ('fold_pos', 'comment', 'lc')


class MyDoubleQuotedScalarString(ruamel.yaml.scalarstring.DoubleQuotedScalarString):
    __slots__ = ('lc')


class MySingleQuotedScalarString(ruamel.yaml.scalarstring.SingleQuotedScalarString):
    __slots__ = ('lc')


class MyPlainScalarString(ruamel.yaml.scalarstring.PlainScalarString):
    __slots__ = ('lc')


class MyScalarInt(ruamel.yaml.scalarint.ScalarInt):
    lc = None


class MyScalarBoolean(ruamel.yaml.scalarbool.ScalarBoolean):
    lc = None


class MyConstructor(ruamel.yaml.constructor.RoundTripConstructor):

    def __init__(self, preserve_quotes=None, loader=None):
        super(MyConstructor, self).__init__(preserve_quotes=preserve_quotes, loader=loader)
        if not hasattr(self.loader, 'comment_handling'):
            self.loader.comment_handling = None

    def construct_scalar(self, node):
        # type: (Any) -> Any
        if not isinstance(node, ScalarNode):
            raise ConstructorError(None, None, _F('expected a scalar node, but found {node_id!s}', node_id=node.id),
                                   node.start_mark,)
        ret_val = None
        if node.style == '|' and isinstance(node.value, str):
            lss = MyLiteralScalarString(node.value, anchor=node.anchor)
            if self.loader and self.loader.comment_handling is None:
                if node.comment and node.comment[1]:
                    lss.comment = node.comment[1][0]  # type: ignore
            else:
                # NEWCMNT
                if node.comment is not None and node.comment[1]:
                    # nprintf('>>>>nc1', node.comment)
                    # EOL comment after |
                    lss.comment = self.comment(node.comment[1][0])  # type: ignore
            ret_val = lss
        elif node.style == '>' and isinstance(node.value, str):
            fold_positions = []  # type: List[int]
            idx = -1
            while True:
                idx = node.value.find('\a', idx + 1)
                if idx < 0:
                    break
                fold_positions.append(idx - len(fold_positions))
            fss = MyFoldedScalarString(node.value.replace('\a', ''), anchor=node.anchor)
            if self.loader and self.loader.comment_handling is None:
                if node.comment and node.comment[1]:
                    fss.comment = node.comment[1][0]  # type: ignore
            else:
                # NEWCMNT
                if node.comment is not None and node.comment[1]:
                    # nprintf('>>>>nc2', node.comment)
                    # EOL comment after >
                    fss.comment = self.comment(node.comment[1][0])  # type: ignore
            if fold_positions:
                fss.fold_pos = fold_positions  # type: ignore
            ret_val = fss
        elif bool(self._preserve_quotes) and isinstance(node.value, str):
            if node.style == "'":
                ret_val = MySingleQuotedScalarString(node.value, anchor=node.anchor)
            if node.style == '"':
                ret_val = MyDoubleQuotedScalarString(node.value, anchor=node.anchor)
        if not ret_val:
            if node.anchor:
                ret_val = MyPlainScalarString(node.value, anchor=node.anchor)
            else:
                ret_val = MyPlainScalarString(node.value)
        ret_val.lc = ruamel.yaml.comments.LineCol()
        ret_val.lc.line = node.start_mark.line
        ret_val.lc.col = node.start_mark.column
        return ret_val

    def construct_yaml_int(self, node):
        # type: (Any) -> Any
        width = None  # type: Any
        value_su = self.construct_scalar(node)
        try:
            sx = value_su.rstrip('_')
            underscore = [len(sx) - sx.rindex('_') - 1, False, False]  # type: Any
        except ValueError:
            underscore = None
        except IndexError:
            underscore = None
        value_s = value_su.replace('_', "")
        sign = +1
        # Assuming that I have only "normal" positive int in my case
        """
        if value_s[0] == '-':
            sign = -1
        if value_s[0] in '+-':
            value_s = value_s[1:]
        if value_s == '0':
            ret_val = 0
        elif value_s.startswith('0b'):
            if self.resolver.processing_version > (1, 1) and value_s[2] == '0':
                width = len(value_s[2:])
            if underscore is not None:
                underscore[1] = value_su[2] == '_'
                underscore[2] = len(value_su[2:]) > 1 and value_su[-1] == '_'
            ret_val = BinaryInt(sign * int(value_s[2:], 2), width=width, underscore=underscore, anchor=node.anchor)
        elif value_s.startswith('0x'):
            # default to lower-case if no a-fA-F in string
            if self.resolver.processing_version > (1, 1) and value_s[2] == '0':
                width = len(value_s[2:])
            hex_fun = HexInt  # type: Any
            for ch in value_s[2:]:
                if ch in 'ABCDEF':  # first non-digit is capital
                    hex_fun = HexCapsInt
                    break
                if ch in 'abcdef':
                    break
            if underscore is not None:
                underscore[1] = value_su[2] == '_'
                underscore[2] = len(value_su[2:]) > 1 and value_su[-1] == '_'
            return hex_fun(
                sign * int(value_s[2:], 16),
                width=width,
                underscore=underscore,
                anchor=node.anchor,
            )
        elif value_s.startswith('0o'):
            if self.resolver.processing_version > (1, 1) and value_s[2] == '0':
                width = len(value_s[2:])
            if underscore is not None:
                underscore[1] = value_su[2] == '_'
                underscore[2] = len(value_su[2:]) > 1 and value_su[-1] == '_'
            return OctalInt(
                sign * int(value_s[2:], 8),
                width=width,
                underscore=underscore,
                anchor=node.anchor,
            )
        elif self.resolver.processing_version != (1, 2) and value_s[0] == '0':
            return sign * int(value_s, 8)
        elif self.resolver.processing_version != (1, 2) and ':' in value_s:
            digits = [int(part) for part in value_s.split(':')]
            digits.reverse()
            base = 1
            value = 0
            for digit in digits:
                value += digit * base
                base *= 60
            return sign * value
        elif self.resolver.processing_version > (1, 1) and value_s[0] == '0':
            # not an octal, an integer with leading zero(s)
            if underscore is not None:
                # cannot have a leading underscore
                underscore[2] = len(value_su) > 1 and value_su[-1] == '_'
            return ScalarInt(sign * int(value_s), width=len(value_s), underscore=underscore)
        elif underscore:
            # cannot have a leading underscore
            underscore[2] = len(value_su) > 1 and value_su[-1] == '_'
            return ScalarInt(
                sign * int(value_s), width=None, underscore=underscore, anchor=node.anchor
            )
        elif node.anchor:
            return ScalarInt(sign * int(value_s), width=None, anchor=node.anchor)
        else:
        """
        ret_val = MyScalarInt(sign * int(value_s))
        ret_val.lc = ruamel.yaml.comments.LineCol()
        ret_val.lc.line = node.start_mark.line
        ret_val.lc.col = node.start_mark.column
        return ret_val

    def construct_yaml_bool(self, node):
        # type: (Any) -> Any
        b = super(MyConstructor, self).construct_yaml_bool(node)
        if node.anchor:
            ret_val = MyScalarBoolean(b, anchor=node.anchor)
        else:
            ret_val = MyScalarBoolean(b)
        ret_val.lc = ruamel.yaml.comments.LineCol()
        ret_val.lc.line = node.start_mark.line
        ret_val.lc.col = node.start_mark.column
        return ret_val


MyConstructor.add_constructor('tag:yaml.org,2002:int', MyConstructor.construct_yaml_int)
MyConstructor.add_constructor('tag:yaml.org,2002:bool', MyConstructor.construct_yaml_bool)

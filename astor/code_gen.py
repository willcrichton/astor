# -*- coding: utf-8 -*-
"""
Part of the astor library for Python AST manipulation.

License: 3-clause BSD

Copyright (c) 2008      Armin Ronacher
Copyright (c) 2012-2017 Patrick Maupin
Copyright (c) 2013-2017 Berker Peksag

This module converts an AST into Python source code.

Before being version-controlled as part of astor,
this code came from here (in 2012):

    https://gist.github.com/1250562

"""

import ast
import math
import sys

from .op_util import get_op_symbol, get_op_precedence, Precedence
from .node_util import ExplicitNodeVisitor
from .string_repr import pretty_string
from .source_repr import pretty_source


def to_source(node, indent_with=' ' * 4, add_line_information=False,
              pretty_string=pretty_string, pretty_source=pretty_source,
              source_generator_class=None):
    """This function can convert a node tree back into python sourcecode.
    This is useful for debugging purposes, especially if you're dealing with
    custom asts not generated by python itself.

    It could be that the sourcecode is evaluable when the AST itself is not
    compilable / evaluable.  The reason for this is that the AST contains some
    more data than regular sourcecode does, which is dropped during
    conversion.

    Each level of indentation is replaced with `indent_with`.  Per default this
    parameter is equal to four spaces as suggested by PEP 8, but it might be
    adjusted to match the application's styleguide.

    If `add_line_information` is set to `True` comments for the line numbers
    of the nodes are added to the output.  This can be used to spot wrong line
    number information of statement nodes.

    `source_generator_class` defaults to `SourceGenerator`, and specifies the
    class that will be instantiated and used to generate the source code.

    """
    if source_generator_class is None:
        source_generator_class = SourceGenerator
    elif not issubclass(source_generator_class, SourceGenerator):
        raise TypeError('source_generator_class should be a subclass of SourceGenerator')
    elif not callable(source_generator_class):
        raise TypeError('source_generator_class should be a callable')
    generator = source_generator_class(
        indent_with, add_line_information, pretty_string)
    generator.visit(node)
    generator.result.append('\n')
    if set(generator.result[0]) == set('\n'):
        generator.result[0] = ''
    return pretty_source(generator.result)


def precedence_setter(AST=ast.AST, get_op_precedence=get_op_precedence,
                      isinstance=isinstance, list=list):
    """ This only uses a closure for performance reasons,
        to reduce the number of attribute lookups.  (set_precedence
        is called a lot of times.)
    """

    def set_precedence(value, *nodes):
        """Set the precedence (of the parent) into the children.
        """
        if isinstance(value, AST):
            value = get_op_precedence(value)
        for node in nodes:
            if isinstance(node, AST):
                node._pp = value
            elif isinstance(node, list):
                set_precedence(value, *node)
            else:
                assert node is None, node

    return set_precedence


set_precedence = precedence_setter()


class Delimit(object):
    """A context manager that can add enclosing
       delimiters around the output of a
       SourceGenerator method.  By default, the
       parentheses are added, but the enclosed code
       may set discard=True to get rid of them.
    """

    discard = False

    def __init__(self, tree, *args):
        """ use write instead of using result directly
            for initial data, because it may flush
            preceding data into result.
        """
        delimiters = '()'
        node = None
        op = None
        for arg in args:
            if isinstance(arg, ast.AST):
                if node is None:
                    node = arg
                else:
                    op = arg
            else:
                delimiters = arg
        tree.write(delimiters[0])
        result = self.result = tree.result
        self.index = len(result)
        self.closing = delimiters[1]
        if node is not None:
            self.p = p = get_op_precedence(op or node)
            self.pp = pp = tree.get__pp(node)
            self.discard = p >= pp

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        result = self.result
        start = self.index - 1
        if self.discard:
            result[start] = ''
        else:
            result.append(self.closing)


class SourceGenerator(ExplicitNodeVisitor):
    """This visitor is able to transform a well formed syntax tree into Python
    sourcecode.

    For more details have a look at the docstring of the `node_to_source`
    function.

    """

    using_unicode_literals = False

    def __init__(self, indent_with, add_line_information=False,
                 pretty_string=pretty_string,
                 # constants
                 len=len, isinstance=isinstance, callable=callable):
        self.result = []
        self.indent_with = indent_with
        self.add_line_information = add_line_information
        self.indentation = 0  # Current indentation level
        self.new_lines = 0  # Number of lines to insert before next code
        self.colinfo = 0, 0  # index in result of string containing linefeed, and
                             # position of last linefeed in that string
        self.pretty_string = pretty_string
        AST = ast.AST

        visit = self.visit
        result = self.result
        append = result.append

        def write(*params):
            """ self.write is a closure for performance (to reduce the number
                of attribute lookups).
            """
            for item in params:
                if isinstance(item, AST):
                    visit(item)
                elif callable(item):
                    item()
                else:
                    if self.new_lines:
                        append('\n' * self.new_lines)
                        self.colinfo = len(result), 0
                        append(self.indent_with * self.indentation)
                        self.new_lines = 0
                    if item:
                        append(item)

        self.write = write

    def __getattr__(self, name, defaults=dict(keywords=(),
                    _pp=Precedence.highest).get):
        """ Get an attribute of the node.
            like dict.get (returns None if doesn't exist)
        """
        if not name.startswith('get_'):
            raise AttributeError
        geta = getattr
        shortname = name[4:]
        default = defaults(shortname)

        def getter(node):
            return geta(node, shortname, default)

        setattr(self, name, getter)
        return getter

    def delimit(self, *args):
        return Delimit(self, *args)

    def conditional_write(self, *stuff):
        if stuff[-1] is not None:
            self.write(*stuff)
            # Inform the caller that we wrote
            return True

    def newline(self, node=None, extra=0):
        self.new_lines = max(self.new_lines, 1 + extra)
        if node is not None and self.add_line_information:
            self.write('# line: %s' % node.lineno)
            self.new_lines = 1

    def body(self, statements):
        self.indentation += 1
        self.write(*statements)
        self.indentation -= 1

    def else_body(self, elsewhat):
        if elsewhat:
            self.write(self.newline, 'else:')
            self.body(elsewhat)

    def body_or_else(self, node):
        self.body(node.body)
        self.else_body(node.orelse)

    def visit_arguments(self, node):
        want_comma = []

        def write_comma():
            if want_comma:
                self.write(', ')
            else:
                want_comma.append(True)

        def loop_args(args, defaults):
            set_precedence(Precedence.Comma, defaults)
            padding = [None] * (len(args) - len(defaults))
            for arg, default in zip(args, padding + defaults):
                self.write(write_comma, arg)
                self.conditional_write('=', default)

        posonlyargs = getattr(node, 'posonlyargs', [])
        offset = 0
        if posonlyargs:
            offset += len(node.defaults) - len(node.args)
            loop_args(posonlyargs, node.defaults[:offset])
            self.write(write_comma, '/')

        loop_args(node.args, node.defaults[offset:])
        self.conditional_write(write_comma, '*', node.vararg)

        kwonlyargs = self.get_kwonlyargs(node)
        if kwonlyargs:
            if node.vararg is None:
                self.write(write_comma, '*')
            loop_args(kwonlyargs, node.kw_defaults)
        self.conditional_write(write_comma, '**', node.kwarg)

    def statement(self, node, *params, **kw):
        self.newline(node)
        self.write(*params)

    def decorators(self, node, extra):
        self.newline(extra=extra)
        for decorator in node.decorator_list:
            self.statement(decorator, '@', decorator)

    def comma_list(self, items, trailing=False):
        set_precedence(Precedence.Comma, *items)
        for idx, item in enumerate(items):
            self.write(', ' if idx else '', item)
        self.write(',' if trailing else '')

    # Statements

    def visit_Assign(self, node):
        set_precedence(node, node.value, *node.targets)
        self.newline(node)
        for target in node.targets:
            self.write(target, ' = ')
        self.visit(node.value)

    def visit_AugAssign(self, node):
        set_precedence(node, node.value, node.target)
        self.statement(node, node.target, get_op_symbol(node.op, ' %s= '),
                       node.value)

    def visit_AnnAssign(self, node):
        set_precedence(node, node.target, node.annotation)
        set_precedence(Precedence.Comma, node.value)
        need_parens = isinstance(node.target, ast.Name) and not node.simple
        begin = '(' if need_parens else ''
        end = ')' if need_parens else ''
        self.statement(node, begin, node.target, end, ': ', node.annotation)
        self.conditional_write(' = ', node.value)

    def visit_ImportFrom(self, node):
        self.statement(node, 'from ', node.level * '.',
                       node.module or '', ' import ')
        self.comma_list(node.names)
        # Goofy stuff for Python 2.7 _pyio module
        if node.module == '__future__' and 'unicode_literals' in (
                x.name for x in node.names):
            self.using_unicode_literals = True

    def visit_Import(self, node):
        self.statement(node, 'import ')
        self.comma_list(node.names)

    def visit_Expr(self, node):
        set_precedence(node, node.value)
        self.statement(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node, is_async=False):
        prefix = 'async ' if is_async else ''
        self.decorators(node, 1 if self.indentation else 2)
        self.statement(node, '%sdef %s' % (prefix, node.name), '(')
        self.visit_arguments(node.args)
        self.write(')')
        self.conditional_write(' ->', self.get_returns(node))
        self.write(':')
        self.body(node.body)
        if not self.indentation:
            self.newline(extra=2)

    # introduced in Python 3.5
    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node, is_async=True)

    def visit_ClassDef(self, node):
        have_args = []

        def paren_or_comma():
            if have_args:
                self.write(', ')
            else:
                have_args.append(True)
                self.write('(')

        self.decorators(node, 2)
        self.statement(node, 'class %s' % node.name)
        for base in node.bases:
            self.write(paren_or_comma, base)
        # keywords not available in early version
        for keyword in self.get_keywords(node):
            self.write(paren_or_comma, keyword.arg or '',
                       '=' if keyword.arg else '**', keyword.value)
        self.conditional_write(paren_or_comma, '*', self.get_starargs(node))
        self.conditional_write(paren_or_comma, '**', self.get_kwargs(node))
        self.write(have_args and '):' or ':')
        self.body(node.body)
        if not self.indentation:
            self.newline(extra=2)

    def visit_If(self, node):
        set_precedence(node, node.test)
        self.statement(node, 'if ', node.test, ':')
        self.body(node.body)
        while True:
            else_ = node.orelse
            if len(else_) == 1 and isinstance(else_[0], ast.If):
                node = else_[0]
                set_precedence(node, node.test)
                self.write(self.newline, 'elif ', node.test, ':')
                self.body(node.body)
            else:
                self.else_body(else_)
                break

    def visit_For(self, node, is_async=False):
        set_precedence(node, node.target)
        prefix = 'async ' if is_async else ''
        self.statement(node, '%sfor ' % prefix,
                       node.target, ' in ', node.iter, ':')
        self.body_or_else(node)

    # introduced in Python 3.5
    def visit_AsyncFor(self, node):
        self.visit_For(node, is_async=True)

    def visit_While(self, node):
        set_precedence(node, node.test)
        self.statement(node, 'while ', node.test, ':')
        self.body_or_else(node)

    def visit_With(self, node, is_async=False):
        prefix = 'async ' if is_async else ''
        self.statement(node, '%swith ' % prefix)
        if hasattr(node, "context_expr"):  # Python < 3.3
            self.visit_withitem(node)
        else:                              # Python >= 3.3
            self.comma_list(node.items)
        self.write(':')
        self.body(node.body)

    # new for Python 3.5
    def visit_AsyncWith(self, node):
        self.visit_With(node, is_async=True)

    # new for Python 3.3
    def visit_withitem(self, node):
        self.write(node.context_expr)
        self.conditional_write(' as ', node.optional_vars)

    # deprecated in Python 3.8
    def visit_NameConstant(self, node):
        self.write(repr(node.value))

    def visit_Pass(self, node):
        self.statement(node, 'pass')

    def visit_Print(self, node):
        # XXX: python 2.6 only
        self.statement(node, 'print ')
        values = node.values
        if node.dest is not None:
            self.write(' >> ')
            values = [node.dest] + node.values
        self.comma_list(values, not node.nl)

    def visit_Delete(self, node):
        self.statement(node, 'del ')
        self.comma_list(node.targets)

    def visit_TryExcept(self, node):
        self.statement(node, 'try:')
        self.body(node.body)
        self.write(*node.handlers)
        self.else_body(node.orelse)

    # new for Python 3.3
    def visit_Try(self, node):
        self.statement(node, 'try:')
        self.body(node.body)
        self.write(*node.handlers)
        self.else_body(node.orelse)
        if node.finalbody:
            self.statement(node, 'finally:')
            self.body(node.finalbody)

    def visit_ExceptHandler(self, node):
        self.statement(node, 'except')
        if self.conditional_write(' ', node.type):
            self.conditional_write(' as ', node.name)
        self.write(':')
        self.body(node.body)

    def visit_TryFinally(self, node):
        self.statement(node, 'try:')
        self.body(node.body)
        self.statement(node, 'finally:')
        self.body(node.finalbody)

    def visit_Exec(self, node):
        dicts = node.globals, node.locals
        dicts = dicts[::-1] if dicts[0] is None else dicts
        self.statement(node, 'exec ', node.body)
        self.conditional_write(' in ', dicts[0])
        self.conditional_write(', ', dicts[1])

    def visit_Assert(self, node):
        set_precedence(node, node.test, node.msg)
        self.statement(node, 'assert ', node.test)
        self.conditional_write(', ', node.msg)

    def visit_Global(self, node):
        self.statement(node, 'global ', ', '.join(node.names))

    def visit_Nonlocal(self, node):
        self.statement(node, 'nonlocal ', ', '.join(node.names))

    def visit_Return(self, node):
        set_precedence(node, node.value)
        self.statement(node, 'return')
        self.conditional_write(' ', node.value)

    def visit_Break(self, node):
        self.statement(node, 'break')

    def visit_Continue(self, node):
        self.statement(node, 'continue')

    def visit_Raise(self, node):
        # XXX: Python 2.6 / 3.0 compatibility
        self.statement(node, 'raise')
        if self.conditional_write(' ', self.get_exc(node)):
            self.conditional_write(' from ', node.cause)
        elif self.conditional_write(' ', self.get_type(node)):
            set_precedence(node, node.inst)
            self.conditional_write(', ', node.inst)
            self.conditional_write(', ', node.tback)

    # Expressions

    def visit_Attribute(self, node):
        self.write(node.value, '.', node.attr)

    def visit_Call(self, node, len=len):
        write = self.write
        want_comma = []

        def write_comma():
            if want_comma:
                write(', ')
            else:
                want_comma.append(True)

        args = node.args
        keywords = node.keywords
        starargs = self.get_starargs(node)
        kwargs = self.get_kwargs(node)
        numargs = len(args) + len(keywords)
        numargs += starargs is not None
        numargs += kwargs is not None
        p = Precedence.Comma if numargs > 1 else Precedence.call_one_arg
        set_precedence(p, *args)
        self.visit(node.func)
        write('(')
        for arg in args:
            write(write_comma, arg)

        set_precedence(Precedence.Comma, *(x.value for x in keywords))
        for keyword in keywords:
            # a keyword.arg of None indicates dictionary unpacking
            # (Python >= 3.5)
            arg = keyword.arg or ''
            write(write_comma, arg, '=' if arg else '**', keyword.value)
        # 3.5 no longer has these
        self.conditional_write(write_comma, '*', starargs)
        self.conditional_write(write_comma, '**', kwargs)
        write(')')

    def visit_Name(self, node):
        self.write(node.id)

    # ast.Constant is new in Python 3.6 and it replaces ast.Bytes,
    # ast.Ellipsis, ast.NameConstant, ast.Num, ast.Str in Python 3.8
    def visit_Constant(self, node):
        value = node.value

        if isinstance(value, (int, float, complex)):
            with self.delimit(node):
                self._handle_numeric_constant(value)
        elif isinstance(value, str):
            self._handle_string_constant(node, node.value)
        elif value is Ellipsis:
            self.write('...')
        else:
            self.write(repr(value))

    def visit_JoinedStr(self, node):
        self._handle_string_constant(node, None, is_joined=True)

    def _handle_string_constant(self, node, value, is_joined=False):
        # embedded is used to control when we might want
        # to use a triple-quoted string.  We determine
        # if we are in an assignment and/or in an expression
        precedence = self.get__pp(node)
        embedded = ((precedence > Precedence.Expr) +
                    (precedence >= Precedence.Assign))

        # Flush any pending newlines, because we're about
        # to severely abuse the result list.
        self.write('')
        result = self.result

        # Calculate the string representing the line
        # we are working on, up to but not including
        # the string we are adding.

        res_index, str_index = self.colinfo
        current_line = self.result[res_index:]
        if str_index:
            current_line[0] = current_line[0][str_index:]
        current_line = ''.join(current_line)

        has_ast_constant = sys.version_info >= (3, 6)

        if is_joined:
            # Handle new f-strings.  This is a bit complicated, because
            # the tree can contain subnodes that recurse back to JoinedStr
            # subnodes...

            def recurse(node):
                for value in node.values:
                    if isinstance(value, ast.Str):
                        # Double up braces to escape them.
                        self.write(value.s.replace('{', '{{').replace('}', '}}'))
                    elif isinstance(value, ast.FormattedValue):
                        with self.delimit('{}'):
                            # expr_text used for f-string debugging syntax.
                            if getattr(value, 'expr_text', None):
                                self.write(value.expr_text)
                            else:
                                set_precedence(value, value.value)
                                self.visit(value.value)
                            if value.conversion != -1:
                                self.write('!%s' % chr(value.conversion))
                            if value.format_spec is not None:
                                self.write(':')
                                recurse(value.format_spec)
                    elif has_ast_constant and isinstance(value, ast.Constant):
                        self.write(value.value)
                    else:
                        kind = type(value).__name__
                        assert False, 'Invalid node %s inside JoinedStr' % kind

            index = len(result)
            recurse(node)

            # Flush trailing newlines (so that they are part of mystr)
            self.write('')
            mystr = ''.join(result[index:])
            del result[index:]
            self.colinfo = res_index, str_index  # Put it back like we found it
            uni_lit = False  # No formatted byte strings

        else:
            assert value is not None, "Node value cannot be None"
            mystr = value
            uni_lit = self.using_unicode_literals

        mystr = self.pretty_string(mystr, embedded, current_line, uni_lit)

        if is_joined:
            mystr = 'f' + mystr
        elif getattr(node, 'kind', False):
            # Constant.kind is a Python 3.8 addition.
            mystr = node.kind + mystr

        self.write(mystr)

        lf = mystr.rfind('\n') + 1
        if lf:
            self.colinfo = len(result) - 1, lf

    # deprecated in Python 3.8
    def visit_Str(self, node):
        self._handle_string_constant(node, node.s)

    # deprecated in Python 3.8
    def visit_Bytes(self, node):
        self.write(repr(node.s))

    def _handle_numeric_constant(self, value):
        x = value

        def part(p, imaginary):
            # Represent infinity as 1e1000 and NaN as 1e1000-1e1000.
            s = 'j' if imaginary else ''
            try:
                if math.isinf(p):
                    if p < 0:
                        return '-1e1000' + s
                    return '1e1000' + s
                if math.isnan(p):
                    return '(1e1000%s-1e1000%s)' % (s, s)
            except OverflowError:
                # math.isinf will raise this when given an integer
                # that's too large to convert to a float.
                pass
            return repr(p) + s

        real = part(x.real if isinstance(x, complex) else x, imaginary=False)
        if isinstance(x, complex):
            imag = part(x.imag, imaginary=True)
            if x.real == 0:
                s = imag
            elif x.imag == 0:
                s = '(%s+0j)' % real
            else:
                # x has nonzero real and imaginary parts.
                s = '(%s%s%s)' % (real, ['+', ''][imag.startswith('-')], imag)
        else:
            s = real
        self.write(s)

    def visit_Num(self, node,
                  # constants
                  new=sys.version_info >= (3, 0)):
        with self.delimit(node) as delimiters:
            self._handle_numeric_constant(node.n)

            # We can leave the delimiters handling in visit_Num
            # since this is meant to handle a Python 2.x specific
            # issue and ast.Constant exists only in 3.6+

            # The Python 2.x compiler merges a unary minus
            # with a number.  This is a premature optimization
            # that we deal with here...
            if not new and delimiters.discard:
                if not isinstance(node.n, complex) and node.n < 0:
                    pow_lhs = Precedence.Pow + 1
                    delimiters.discard = delimiters.pp != pow_lhs
                else:
                    op = self.get__p_op(node)
                    delimiters.discard = not isinstance(op, ast.USub)

    def visit_Tuple(self, node):
        with self.delimit(node) as delimiters:
            # Two things are special about tuples:
            #   1) We cannot discard the enclosing parentheses if empty
            #   2) We need the trailing comma if only one item
            elts = node.elts
            delimiters.discard = delimiters.discard and elts
            self.comma_list(elts, len(elts) == 1)

    def visit_List(self, node):
        with self.delimit('[]'):
            self.comma_list(node.elts)

    def visit_Set(self, node):
        if node.elts:
            with self.delimit('{}'):
                self.comma_list(node.elts)
        else:
            # If we tried to use "{}" to represent an empty set, it would be
            # interpreted as an empty dictionary. We can't use "set()" either
            # because the name "set" might be rebound.
            self.write('{1}.__class__()')

    def visit_Dict(self, node):
        set_precedence(Precedence.Comma, *node.values)
        with self.delimit('{}'):
            for idx, (key, value) in enumerate(zip(node.keys, node.values)):
                self.write(', ' if idx else '',
                           key if key else '',
                           ': ' if key else '**', value)

    def visit_BinOp(self, node):
        op, left, right = node.op, node.left, node.right
        with self.delimit(node, op) as delimiters:
            ispow = isinstance(op, ast.Pow)
            p = delimiters.p
            set_precedence((Precedence.Pow + 1) if ispow else p, left)
            set_precedence(Precedence.PowRHS if ispow else (p + 1), right)
            self.write(left, get_op_symbol(op, ' %s '), right)

    def visit_BoolOp(self, node):
        with self.delimit(node, node.op) as delimiters:
            op = get_op_symbol(node.op, ' %s ')
            set_precedence(delimiters.p + 1, *node.values)
            for idx, value in enumerate(node.values):
                self.write(idx and op or '', value)

    def visit_Compare(self, node):
        with self.delimit(node, node.ops[0]) as delimiters:
            set_precedence(delimiters.p + 1, node.left, *node.comparators)
            self.visit(node.left)
            for op, right in zip(node.ops, node.comparators):
                self.write(get_op_symbol(op, ' %s '), right)

    # assignment expressions; new for Python 3.8
    def visit_NamedExpr(self, node):
        with self.delimit(node) as delimiters:
            p = delimiters.p
            set_precedence(p, node.target)
            set_precedence(p + 1, node.value)
            # Python is picky about delimiters for assignment
            # expressions: it requires at least one pair in any
            # statement that uses an assignment expression, even
            # when not necessary according to the precedence
            # rules. We address this with the kludge of forcing a
            # pair of parentheses around every assignment
            # expression.
            delimiters.discard = False
            self.write(node.target, ' := ', node.value)

    def visit_UnaryOp(self, node):
        with self.delimit(node, node.op) as delimiters:
            set_precedence(delimiters.p, node.operand)
            # In Python 2.x, a unary negative of a literal
            # number is merged into the number itself.  This
            # bit of ugliness means it is useful to know
            # what the parent operation was...
            node.operand._p_op = node.op
            sym = get_op_symbol(node.op)
            self.write(sym, ' ' if sym.isalpha() else '', node.operand)

    def visit_Subscript(self, node):
        set_precedence(node, node.slice)
        self.write(node.value, '[', node.slice, ']')

    def visit_Slice(self, node):
        set_precedence(node, node.lower, node.upper, node.step)
        self.conditional_write(node.lower)
        self.write(':')
        self.conditional_write(node.upper)
        if node.step is not None:
            self.write(':')
            if not (isinstance(node.step, ast.Name) and
                    node.step.id == 'None'):
                self.visit(node.step)

    def visit_Index(self, node):
        with self.delimit(node) as delimiters:
            set_precedence(delimiters.p, node.value)
            self.visit(node.value)

    def visit_ExtSlice(self, node):
        dims = node.dims
        set_precedence(node, *dims)
        self.comma_list(dims, len(dims) == 1)

    def visit_Yield(self, node):
        with self.delimit(node):
            set_precedence(get_op_precedence(node) + 1, node.value)
            self.write('yield')
            self.conditional_write(' ', node.value)

    # new for Python 3.3
    def visit_YieldFrom(self, node):
        with self.delimit(node):
            self.write('yield from ', node.value)

    # new for Python 3.5
    def visit_Await(self, node):
        with self.delimit(node):
            self.write('await ', node.value)

    def visit_Lambda(self, node):
        with self.delimit(node) as delimiters:
            set_precedence(delimiters.p, node.body)
            self.write('lambda ')
            self.visit_arguments(node.args)
            self.write(': ', node.body)

    def visit_Ellipsis(self, node):
        self.write('...')

    def visit_ListComp(self, node):
        with self.delimit('[]'):
            self.write(node.elt, *node.generators)

    def visit_GeneratorExp(self, node):
        with self.delimit(node) as delimiters:
            if delimiters.pp == Precedence.call_one_arg:
                delimiters.discard = True
            set_precedence(Precedence.Comma, node.elt)
            self.write(node.elt, *node.generators)

    def visit_SetComp(self, node):
        with self.delimit('{}'):
            self.write(node.elt, *node.generators)

    def visit_DictComp(self, node):
        with self.delimit('{}'):
            self.write(node.key, ': ', node.value, *node.generators)

    def visit_IfExp(self, node):
        with self.delimit(node) as delimiters:
            set_precedence(delimiters.p + 1, node.body, node.test)
            set_precedence(delimiters.p, node.orelse)
            self.write(node.body, ' if ', node.test, ' else ', node.orelse)

    def visit_Starred(self, node):
        self.write('*', node.value)

    def visit_Repr(self, node):
        # XXX: python 2.6 only
        with self.delimit('``'):
            self.visit(node.value)

    def visit_Module(self, node):
        self.write(*node.body)

    visit_Interactive = visit_Module

    def visit_Expression(self, node):
        self.visit(node.body)

    # Helper Nodes

    def visit_arg(self, node):
        self.write(node.arg)
        self.conditional_write(': ', node.annotation)

    def visit_alias(self, node):
        self.write(node.name)
        self.conditional_write(' as ', node.asname)

    def visit_comprehension(self, node):
        set_precedence(node, node.iter, *node.ifs)
        set_precedence(Precedence.comprehension_target, node.target)
        stmt = ' async for ' if self.get_is_async(node) else ' for '
        self.write(stmt, node.target, ' in ', node.iter)
        for if_ in node.ifs:
            self.write(' if ', if_)

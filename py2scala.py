#!/usr/bin/python

# Copyright (C) 2011 Ben Wing <ben@benwing.com>

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# * Redistributions of source code must retain the above copyright notice,
#   this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# * The name of Ben Wing may not be used to endorse or promote products
#   derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import re
import sys
import fileinput
import optparse

###########################################################################
#
# Comments
#
###########################################################################

## General FIXME's for this program:

## -- BUGBUG: In multiline def() with --remove-self, we change 'self' to
##    'this' but then don't remove 'this'.  Works OK with single-line def().
## -- Converting this program to use a proper parser would make some
## conversions easier (e.g. $1 in $2 -> $2 contains $1), and might simplify
## some of the multiline handling.
## -- In variable snarfing/fixing-up (e.g. adding var/val), should:
##    1. Handle cases like (foo, bar) = ..., adding val and noting both
##       variables so we handle later cases where either var is modified
##    2. Handle cases like foo, bar = ..., similarly, but MUST add parens
##       around the variables, i.e. convert to 'val (foo, bar) = ...'
##    3. Handle cases like val/var (foo, bar) = ..., handling similarly,
##       but with the same changes we make whenever we've already seen
##       val/var.
##    4. Handle cases like val/var foo, bar = ..., which are equivalent
##       to assigning both foo and bar the entire RHS.
##    5. (Different subject) Variable declarations introduced inside of
##       braces will scope only to those braces.  If we see a val/var,
##       add it but also note the current indent, so that when popping past
##       that indent level we remove those items.  If we see an assignment
##       to new variable and have to add our own val/var, we need to keep
##       in mind that just adding val/var to the same line will cause the
##       var to have the scope of the enclosing brace.  So we need to
##       track both assignments and references to variables, and if we see
##       either, issue a warning indicating that the code will have to be
##       fixed up manually. (Moving it out automatically is too tricky for
##       what we're doing here.)
## -- Handle XML literals properly in Scala code.

###########################################################################
#
# Command-line options and usage
#
###########################################################################

usage = """%prog [OPTIONS] [FILE]

Convert a Python file to Scala.

Conversion is not perfect, and it is completely expected that further
manual editing is required.  The idea is to take care of the most common
conversions necessary, especially the ones (like adding braces) that are
difficult to do with a simple search/replace.

An important property of this program is that it is (largely) idempotent,
i.e. if you run it on code that has previously been converted, it should
do nothing, and if run on partially converted code it should only convert
the remainder.  This is useful for various reasons.  One of them has to
do with conversions like removing `self.' or changing bracketed array
references to parens that remove useful info.  Eventually we want to make
these changes, but before then we may find the unchanged code useful for
e.g. redoing class constructors (which are done in a completely different
fashion in Python and Scala, and basically need to be converted manually)
and adding type annotations to functions (again something to do manually).
Thus, the program is intended to work as follows:

1. Run it to do a preliminary conversion
2. Fix up class constructors, add type annotations
3. Run it again with the -2 (or --second-pass) option to fix up self
   references and brackets, and not do other changes that might mess up
   previously Scala-fied code (e.g. changing None to null, since None also
   has a meaning in Scala).

Currently, parsing is done with regexps rather than context-free.  This means
that some constructions may not be converted perfectly.  However, strings
of various sorts (including multiline strings) are usually handled properly;
likewise multiline block openers and such.  However, embedded XML is NOT
currently handled properly -- or at least, unquoted raw text will get frobbed
instead of ignored.  You might want to use the PY2SCALA directives to get
around this (see below).

If the conversion process messes up and changes something that you don't
want changed, you can override this using a directive something like this:
  //  !!PY2SCALA: <directive>
or like this:
  #   !!PY2SCALA: <directive>
where <directive> is currently either BEGIN_PASSTHRU (start passing lines
through without trying to frob them) or END_PASSTHRU (end doing this).  Note
that the comment sign at the begin is not part of the directive, but simply
a way of embedding the directive in code.  Likewise the <> signs do not
appear in the directive command, which uses only uppercase letters, the
underscore character, and possibly numbers.  Such a directive will be
recognized anywhere on a line, regardless of what comes before or after --
that way, it can be embedded in a comment or whatever.  However, it will only
be recognized if it has exactly the opening tag "!!PY2SCALA: " followed by a
directive command, and only if the command is one of the recognized ones.
That way it's highly unlikely such a directive would appear by accident.
"""

parser = optparse.OptionParser(usage=usage)
parser.add_option("-s", "--scala", action="store_true",
                   help="""If true, code is already Scala-fied, so don't do
conversions that might negatively affect Scala code.""")
parser.add_option("-r", "--remove-self", "--rs", action="store_true",
                   help="""Remove self.* references and self params.
Also removes cls.* references and cls params.   Not done by default because
it removes info necessary for manually converting classes (especially
constructors) and separating class methods into companion objects.  Useful
to rerun this program with this option after these things have been done.""")
parser.add_option("-b", "--convert-brackets", "--cb", action="store_true",
                   help="""Try to convert array refs like foo[i] to Scala-style foo(i).
Not done by default because it removes info useful for adding type annotations
to functions.  Useful to rerun this program with this option after doing
this.  You will still have to convert slices by hand.  This attempts not
to convert bracket references that are meaningful to Scala (i.e. generic
type parameters) using the assumption that types in Scala begin with an
uppercase letter.""")
parser.add_option("-2", "--second-pass", action="store_true",
                   help="""Equivalent to -srb.  Used when doing a second pass through already Scala-fied code to remove self.* references and convert brackets to parens for array refs.""")

(options, args) = parser.parse_args()
if options.second_pass:
  options.scala = True
  options.remove_self = True
  options.convert_brackets = True

## Process file(s)

def uniprint(text, outfile=sys.stdout, nonl=False, flush=False):
  '''Print text string using 'print', converting Unicode as necessary.
If string is Unicode, automatically convert to UTF-8, so it can be output
without errors.  Send output to the file given in OUTFILE (default is
stdout).  Uses the 'print' command, and normally outputs a newline; but
this can be suppressed using NONL.  Output is not normally flushed (unless
the stream does this automatically); but this can be forced using FLUSH.'''
  
  if type(text) is unicode:
    text = text.encode("utf-8")
  if nonl:
    print >>outfile, text,
  else:
    print >>outfile, text
  if flush:
    outfile.flush()

def errprint(text, nonl=False):
  '''Print text to stderr using 'print', converting Unicode as necessary.
If string is Unicode, automatically convert to UTF-8, so it can be output
without errors.  Uses the 'print' command, and normally outputs a newline; but
this can be suppressed using NONL.'''
  uniprint(text, outfile=sys.stderr, nonl=nonl)

# A hackish function for print-debugging.
def debprint(fmt, *vals):
  errprint("Debug: Line %d, %s" % (lineno, fmt % vals))

# RE's for balanced expressions.  This is a major hack.  We only use this
# for things like converting '$1 in $2' to '$2 contains $1'.  In general,
# we count parens and brackets properly.
balparenexpr = r'\([^()]*\)'
balbracketexpr = r'\[[^\[\]]*\]'
balstr0 = r'(?:[^()\[\]]|%s|%s)*' % (balparenexpr, balbracketexpr)
bal2parenexpr = r'\(%s\)' % balstr0
bal2bracketexpr = r'\[%s\]' % balstr0
bal2str0 = r'(?:[^()\[\]]|%s|%s)*' % (bal2parenexpr, bal2bracketexpr)
bal2strnospace0 = r'(?:[^ ()\[\]]|%s|%s)*' % (bal2parenexpr, bal2bracketexpr)
bal2str = r'(?:[^()\[\]]|%s|%s)+' % (bal2parenexpr, bal2bracketexpr)
bal2strnospace = r'(?:[^ ()\[\]]|%s|%s)+' % (bal2parenexpr, bal2bracketexpr)

if options.scala:
  commentre = r''' | /\* .*? \*/  # C-style Scala comment
                   | /\* .*       # C-style Scala comment unmatched (multi-line)
                   | //.*         # C++-style Scala comment
               '''
else:
  commentre = r''' | [#].*'''     # Python comment

# RE to split off quoted strings and comments.
# FIXME: The handling of backslashes in raw strings is slightly wrong;
# I think we only want to look for a backslashed quote of the right type.
# (Or maybe we look for no backslashes at all?)
# FIXME: We don't handle XML literals at all
stringre = re.compile(r'''(   r?'[']' .*? '[']'   # 3-single-quoted string
                            | r?""" .*? """       # 3-double-quoted string 
                            | r?'[']'.*           # unmatched 3-single-quote
                            | r?""".*             # unmatched 3-double-quote
                            | r?' (?:\\.|[^'])* ' # single-quoted string
                            | r?" (?:\\.|[^"])* " # double-quoted string
                            | r?'.*               # unmatched single-quote
                            | r?".*               # unmatched double-quote
                            %s
                          )''' % commentre, re.X)

# Test function for above RE.  Not called.
def teststr(x):
  split = stringre.split(x)
  for y in split:
    print y

# List of multi-line delimiters (quotes, comments).  Each entry is a tuple
# of (start, end) -- this handles comments like /* ... */ properly.
multi_line_delims = [('"""', '"""'), ("'''", "'''")]
if options.scala:
  multi_line_delims += [('/*', '*/')]
single_quote_delims = ['"', "'"]

# If we added a triple-quote delimiter, remove it. (We add such delimiters
# to the beginning of a line if we're in the middle of a multi-line quote,
# so our string-handling works right.)
def line_no_added_delim(line, delim):
  if delim:
    dlen = len(delim)
    assert line[0:dlen] == delim
    return line[dlen:]
  else:
    return line

# Add a "virtual line", possibly spanning multiple lines, to the line list
def add_bigline(bigline):
  global lines
  if bigline is not None:
    lines += bigline.split('\n')

# Main function to frob the inside of a line.  Passed a line split by
# stringre.split() into alternating text and delimiters composed of
# quoted strings and/or comments.  This is a generator function that
# returns values with `yield'.
def modline(split):
  for i in xrange(len(split)):
    prev = None
    if i > 0:
      prev = split[i-1]
    vv = split[i]
    #debprint("Saw #%d: %s", i, vv)

    # Skip blank sections (e.g. at end of line after a comment)
    if len(vv) == 0:
      yield vv
      continue

    # If we're handling a string composed from the added delimiter,
    # don't try to frob it.
    nofrob = old_openquote and i == 1 and prev == ""

    if i % 2 == 1: # We are looking at a delimiter

      # Look for raw-string prefix on strings
      vv2 = vv
      # raw will be None if no quote of any sort here, 'r' if a raw Python
      # string, '' if non-raw string
      raw = None
      if vv[0] == 'r' and len(vv) > 1 and vv[1] in single_quote_delims:
        vv2 = vv[1:]
        raw = "r"
      elif vv[0] in single_quote_delims:
        raw = ""

      # Look for (unclosed) multi-line quote or comment
      saw_multiline_delim = False
      unclosed = False
      global openquote
      for delim in multi_line_delims:
        (delimstart, delimend) = delim
        if vv2.startswith(delimstart):
          #debprint("Saw multi-line delim %s", delimstart)
          saw_multiline_delim = True
          if vv2 == delimstart or not vv2.endswith(delimend):
            openquote = delimstart
            unclosed = True
      if saw_multiline_delim and not unclosed:
        openquote = None

      if raw is not None: # We're handline some sort of string, frob it
        if saw_multiline_delim:
          # FIXME!! Python has eight types of strings: Single and triple
          # quoted strings, using both single and double quotes (i.e.
          # ', ", ''', """), as well as the "raw" variants prefixed with r.
          # Scala has only two types: Strings quoted like "foo", and
          # raw multiline strings quoted like """foo""".  We're not properly
          # shoehorning the various types of Python strings into Scala
          # strings.  The only thing we do is try to convert Python strings
          # like r"foo" and r'foo' into Scala raw """foo""" strings.
          #
          # Note that Scala also has single-character literals like 'f',
          # whereas Python uses normal strings for this.  We try to do the
          # right thing here (i.e. leave 'f' as 'f' but convert 'ff' to "ff"),
          # but we can't be perfect because (a) we don't know whether a
          # single-character Python string should become a Scala string or
          # character literal, and (b) since we can be run more than once,
          # we can't distinguish "f" as a Scala single-character string
          # (should be left alone) from "f" as a Python single-character
          # string (potentially convertible to Scala 'f').
          if vv2.startswith("'''") and not nofrob:
            if unclosed:
              yield raw + '"""' + vv2[3:]
            else:
              yield raw + '"""' + vv2[3:-3] + '"""'
          else:
            yield vv
          continue
        for delim in single_quote_delims:
          if (vv2.startswith(delim) and
              (vv2 == delim or not vv2.endswith(delim))):
            warning("Saw unfinished single quoted string %s" % vv)
            yield vv
            continue
        revisedstr = vv2
        if vv2.startswith("'") and (
            (len(vv2) != 4 if not raw and vv2[1] == '\\' else len(vv2) != 3)
            ):
          # Single-quoted string of length != 1
          # Convert to double-quoted string
          revisedstr = '"' + vv2[1:-1] + '"'
        # FIXME! This may fail with unbackslashed quotes of the other
        # sort in the string.  See comments above about the eight types of
        # Python strings.
        if raw:
          yield '""' + revisedstr + '""'
        else:
          yield revisedstr
        continue
        # We don't convert in the opposite direction because in Scala
        # someone might reasonably have a double-quoted string of length 1

      # Convert comments
      if vv.startswith('#'):
        yield '//' + vv[1:]
        continue
      yield vv

    else:
      # Not a delimiter

      vv = re.sub(r'\bor\b', '||', vv)
      vv = re.sub(r'\band\b', '&&', vv)
      vv = re.sub(r'\bTrue\b', 'true', vv)
      vv = re.sub(r'\bFalse\b', 'false', vv)
      # some None in Scala code should actually be None (e.g. when
      # Option[T] is used)
      if not options.scala:
        vv = re.sub(r'\bNone\b', 'null', vv)
      vv = re.sub(r'\bnot ', '!', vv)
      vv = re.sub(r'\bis (None|null)\b', '== null', vv)
      vv = re.sub(r'\bis !.*(None|null)\b', '!= null', vv)
      vv = re.sub(r'lambda ([A-Za-z0-9]+): ?', r'\1 => ', vv)
      # Seems this isn't necessary; (for x <- y if foo) works fine in Scala
      #vv = re.sub(r'[\[(](.*) for (.*) in (.*) if (.*)[)\]]',
      #             r'(for (\2 <- \3; if \4) yield \1)', vv)
      vv = re.sub(r'[\[(](%s) for (.*) in (%s)[)\]]' % (bal2str, bal2str),
                   r'(for (\2 <- \3) yield \1)', vv)
      if not re.match(r'.*\bfor\b', vv):
        vv = re.sub(r'(%s) in (%s)\b' % (bal2strnospace, bal2strnospace),
            r'\2 contains \1', vv)
      vv = re.sub(r'len\((%s)\)' % bal2str, r'\1.length', vv)
      vv = re.sub(r'\bpass\b', '()', vv)
      # change % to format but only when applied to string
      if prev and prev[0] in single_quote_delims:
        vv = re.sub(r'^( +)%( +)', r'\1format\2', vv)
      if options.remove_self:
        vv = re.sub(r'\bself\.', '', vv)
        vv = re.sub(r'\bself\b', 'this', vv)
        vv = re.sub(r'\bcls\.', '', vv)
        # Not sure about this
        #vv = re.sub(r'\bcls\b', 'this', vv)
      if options.convert_brackets:
        # Convert bracketed expressions, but avoid list constructors
        # (previous character not alphanumeric) and scala generic vars/types
        # (the type in brackets is usually uppercase)
        vv = re.sub(r'([A-Za-z0-9_])\[([^A-Z\]]%s)\]' % bal2str0,
            r'\1(\2)', vv)

      yield vv

# Indentation of current or latest line
curindent = 0
# If not None, a continuation line (line ending in backslash)
contline = None
# Status of any unclosed multi-line quotes (''' or """) or multi-line comments
# at end of line
openquote = None
# Same, but for the beginning of the line
old_openquote = None
# Mismatch in parens/brackets so far at end of line (includes mismatch from
# previous lines, so that a value of 0 means we are at the end of a logical
# line)
paren_mismatch = 0
# Same, but for the beginning of the line
old_paren_mismatch = 0
# Indent last time paren mismatch was zero
zero_mismatch_indent = 0
# Source line number last time paren mismatch was zero
zero_mismatch_lineno = 0
# Blank/comment count last time paren mistmatch was zero
zero_mismatch_prev_blank_or_comment_line_count = 0
# Accumulation of line across paren mismatches and multi-line quotes.
# This will hold the concatenation of all such lines, so that we can
# properly handle multi-line if/def/etc. statements and variable assignments.
bigline = None
# Accumulation of unfrobbed line across paren mismatches
old_bigline = None
# Lineno and indent at start of bigline
bigline_indent = 0
bigline_lineno = 0
# Current source line number.  Not the same as a "line index", which is an
# index into the lines[] array. (Not even simply off by 1, because we
# add extra lines consisting of braces, and do other such changes.)
lineno = 0
# Lines accumulated so far.  We need to be able to go back and modify old
# lines sometimes.  Note that len(lines) is the "line index" of the
# current line being processed, at least after we handle dedentation
# (where we might be inserting lines).
lines = []
# Number of blank or comment-only lines just seen
blank_or_comment_line_count = 0
# Same, not considering current line
prev_blank_or_comment_line_count = 0
# Whether we are ignoring lines due to PY2SCALA directive
in_ignore_lines = False

# Store information associated with an indentation block (e.g. an
# if/def statement); stored into indents[]
class Indent:
  # startind: Line index of beginning of block-begin statement
  # endind: Line index of end of block-begin statement
  # indent: Indentation of block-begin statement
  # ty: "python" or "scala"
  def __init__(self, startind, endind, indent, ty):
    self.startind = startind
    self.endind = endind
    self.indent = indent
    self.ty = ty

  # Adjust line indices starting at AT up by BY.
  def adjust_lineinds(self, at, by):
    if self.startind >= at: self.startind += by
    if self.endind >= at: self.endind += by

# Store information associated with a class or function definition;
# stored into defs[]
class Define:
  # ty: "class" or "def"
  # name: name of class or def
  # vardict: dict of currently active params and local vars.  The key is
  #   a variable name and the value is one of "val" (unsettable function
  #   parameter), "var" (settable function parameter), "explicit"
  #   (variable declared with an explicit var/val) or a line number
  #   (bare variable assignment; the line number is so that we can change
  #   an added 'val' to 'var' if necessary).
  def __init__(self, ty, name, vardict):
    self.ty = ty
    self.name = name
    self.vardict = vardict
    self.lineno = bigline_lineno
    self.indent = bigline_indent
    self.lineind = len(lines)
    # Line index of insertion point in companion object
    self.compobj_lineind = None

  # Adjust line indices starting at AT up by BY.
  def adjust_lineinds(self, at, by):
    #debprint("Adjusting lines at %s by %s", at, by)
    if self.lineind >= at: self.lineind += by
    if self.compobj_lineind and self.compobj_lineind >= at: self.compobj_lineind += by
    for (k, v) in self.vardict.iteritems():
      if type(v) is int and v >= at: self.vardict[k] += by
    #debprint("Finishing adjusting lines at %s by %s, len(lines)=%s", at, by,
    #    len(lines))
    #for (k, v) in self.vardict.iteritems():
    #  debprint("name=%s, vardict[%s] = %s", self.name, k, v)

# List of currently active indentation blocks, of Indent objects
indents = []
# List, for each currently active function and class define, of Define objects
defs = []

# Adjust line indices starting at AT up by BY.  Used when inserting or
# deleting lines from lines[].
def adjust_lineinds(at, by):
  for d in defs:
    d.adjust_lineinds(at, by)
  for i in indents:
    i.adjust_lineinds(at, by)

# Output a warning for the user.
def warning(text, nonl=False):
  '''Line errprint() but also add "Warning: " and line# to the beginning.'''
  errprint("Warning: %d: %s" % (lineno, text), nonl=nonl)


################# Main loop


# Loop over all lines in stdin or argument(s)
for line in fileinput.input(args):
  lineno += 1

  # Remove LF or CRLF, convert tabs to spaces
  line = line.rstrip("\r\n").expandtabs()
  #debprint("Saw line: %s", line)
  # If previous line was continued, add it to this line
  if contline:
    # This is OK because we checked to make sure continuation was not in
    # a quote or comment
    line = contline.rstrip() + " " + line.lstrip()
    contline = None
                                  
  m = re.match('.*!!PY2SCALA: ([A-Z_]+)', line)
  if m:
    directive = m.group(1)
    if directive == 'BEGIN_PASSTHRU':
      in_ignore_lines = True
      lines += [line]
      continue
    elif directive == 'END_PASSTHRU':
      in_ignore_lines = False
      lines += [line]
      continue
  if in_ignore_lines:
    lines += [line]
    continue

  # If we are continuing a multiline quote, add the delimiter to the
  # beginning.  That way we will parse the line correctly.  We remove
  # the delimiter at the bottom.
  if openquote:
    line = openquote + line
  # Split the line based on quoted and commented sections
  #debprint("Line before splitting: [%s]", line)
  splitline = list(stringre.split(line))

  # If line is continued, don't do anything yet (till we get the whole line)
  lasttext = splitline[-1]
  if lasttext and lasttext[-1] == '\\':
    contline = line_no_added_delim(line, openquote)[0:-1]
    continue

  # Look for blank or comment-only lines
  blankline = re.match(r'^ *$', line)
  if re.match('^ *(#.*|//.*)?$', line):
    blank_or_comment_line_count += 1
  else:
    prev_blank_or_comment_line_count = blank_or_comment_line_count
    blank_or_comment_line_count = 0

  # Record original line, and values of paren_mismatch and openquote
  # at start of line
  old_paren_mismatch = paren_mismatch
  oldline = line
  old_openquote = openquote

  # Count # of mismatched parens (also brackets)
  for i in xrange(len(splitline)):
    vv = splitline[i]
    if i % 2 == 0: # Make sure not a quoted string or comment
      # Count mismatch of parens and brackets.  We don't do braces because
      # we might be processing Scala-like code.
      paren_mismatch += vv.count('(') + vv.count('[') - \
          vv.count(')') - vv.count(']')
  #debprint("Line %d, old paren mismatch %d, new paren mismatch %d",
  #    lineno, old_paren_mismatch, paren_mismatch)
  if paren_mismatch < 0:
    warning("Apparent unmatched right-paren, we might be confused: %s" % line)
    paren_mismatch = 0

  # Compute current indentation, handle dedenting (may need to insert braces).
  # Note that blank lines don't have any effect on indentation in Python,
  # and nor do continued multi-line quotes.
  if not old_openquote and not blankline:
    # Get current indentation
    m = re.match('( *)', line)
    indent = len(m.group(1))

    # Handle dedent: End any blocks as appropriate, and add braces
    if indent < curindent:
      # Pop off all indentation blocks at or more indented than current
      # position, and add right braces
      while indents and indents[-1].indent >= indent:
        indobj = indents.pop()
        # Can happen, e.g., if // is used in Python to mean "integer division",
        # or other circumstances where we got confused
        if old_paren_mismatch > 0:
          warning("Apparent unmatched left-paren somewhere before, possibly line %d, we might be confused" % zero_mismatch_lineno)
          # Reset to only mismatched left parens on this line
          paren_mismatch = paren_mismatch - old_paren_mismatch
          if paren_mismatch < 0:
            paren_mismatch = 0
        if indobj.ty == "scala":
          continue
        rbrace = "%s}" % (' '*indobj.indent)
        # Check for right brace already present; if so, just make sure
        # corresponding left brace is present
        if line.startswith(rbrace):
          lines[indobj.endind] += " {"
        else:
          insertpos = len(lines)
          # Insert the right brace *before* any blank lines (we skipped over
          # them since they don't affect indentation)
          while re.match('^ *$', lines[insertpos - 1]):
            insertpos -= 1
          # If the "block" is only a single line, and it's not introduced
          # by "def" or "class", don't add braces.
          # We check for 2 because with a single-line block, the potential
          # right-brace insertion point is 2 lines past the opening block
          # (1 for opening line itself, 1 for block)
          #debprint("lineno:%s, startind:%s, endind:%s, lines:%s",
          #    lineno, indobj.startind,
          #    indobj.endind, len(lines))
          if (insertpos - indobj.endind > 2 or
              re.match('^ *(def|class) ', lines[indobj.startind])):
            lines[indobj.endind] += " {"
            lines[insertpos:insertpos] = [rbrace]
      # Pop off all function definitions that have been closed
      while defs and defs[-1].indent >= indent:
        defs.pop()
    # Set indentation value for current line
    curindent = indent

  # Record some values if no paren mismatch or continued quote at start of line
  if not old_openquote and old_paren_mismatch == 0:
    zero_mismatch_indent = curindent
    zero_mismatch_lineno = lineno
    zero_mismatch_prev_blank_or_comment_line_count = prev_blank_or_comment_line_count

  ########## Now we modify the line itself

  # Frob the line in various ways (e.g. change 'and' to '&&')
  line = ''.join(modline(splitline))

  # Accumulate a logical line into 'bigline' across unmatched parens and quotes
  line_without_delim = line_no_added_delim(line, old_openquote)
  old_line_without_delim = line_no_added_delim(oldline, old_openquote)
  if old_paren_mismatch == 0 and not old_openquote:
    assert bigline == None
    bigline = line_without_delim
    old_bigline = old_line_without_delim
    bigline_indent = curindent
    bigline_lineno = lineno
    assert bigline_indent == zero_mismatch_indent
    assert bigline_lineno == zero_mismatch_lineno
  else:
    bigline = bigline + "\n" + line_without_delim
    old_bigline = old_bigline + "\n" + old_line_without_delim

  # If we see a Scala-style opening block, just note it; important for
  # unmatched-paren handling above (in particular where we reset the
  # unmatched-paren count at the beginning of a block, to deal with
  # errors in parsing)
  if paren_mismatch == 0 and not openquote and (
      re.match(r'.*\{ *$', splitline[-1])):
    indents += [Indent(len(lines), len(lines) + (bigline or "").count('\n'),
      zero_mismatch_indent, "scala")]

  # Error recovery.  If we see a Python block opening, and we're not in
  # a continued quote, and we were inside a parened or bracketed expr,
  # something is probably wrong, so reset paren count.
  if not old_openquote and old_paren_mismatch > 0 and \
      re.match(r' *(if|for|with|while|try|elif|else|except|def|class) +.*:.*$',
               line):
    # Can happen, e.g., if // is used in Python to mean "integer division"
    # but we interpret it as a comment (so a closing paren gets ignored),
    # or other circumstances where we got confused or the user actually
    # messed up their parens
    warning("Apparent unmatched left-paren somewhere before, possibly line %d, we might be confused" % zero_mismatch_lineno)
    # Reset to only mismatched parens on this line
    paren_mismatch = paren_mismatch - old_paren_mismatch
    if paren_mismatch < 0:
      paren_mismatch = 0
    # Restart the logical line, add any old line to lines[]
    add_bigline(bigline)
    bigline = line
    old_bigline = oldline

  # Skip to next line if this line doesn't really end
  if paren_mismatch > 0 or openquote:
    continue

  # Remove self and cls parameters from def(), if called for
  # Note that we changed 'self' to 'this' above
  if options.remove_self:
    m = re.match(r'^(\s*def\s+[A-Za-z0-9_]+\s*)\((?:\s*(?:this|cls)\s*)(\)|, *)(.*)$', bigline)
    if m:
      if m.group(2) == ')':
        bigline = '%s()%s' % (m.group(1), m.group(3))
      else:
        bigline = '%s(%s' % (m.group(1), m.group(3))
    if re.match(r'^ *def +__init__\(', bigline):
      warning("Need to convert to Scala constructor: %s" % bigline)

  ######### Handle blocks.
  
  front, body, back = "", "", ""
  frontbody = bigline
  # Look for a Python statement introducing a block.  Split off leading
  # indentation and trailing spaces.
  m = re.match(r'(\s*)(.*?)\s*:\s*$', frontbody, re.S)
  if m:
    front, body = m.groups()
  else:
    splits = re.split(r'(#|//)', line, 1)
    if len(splits) == 3:
      frontbody = splits[0]
      newback = splits[1] + splits[2]
      m = re.match(r'''(\s*)([^\'\"]*?)\s*:\s*$''', frontbody, re.S)
      if m:
        front, body = m.groups()
        back = " " + newback

  # FIXME: Don't yet handle single-line if statements, e.g. 'if foo: bar'

  # Check for def/class and note function arguments.  We do this separately
  # from the def check below so we find both def and class, and both
  # Scala and Python style.

  m = re.match('\s*(def|class)\s+(.*?)(?:\((.*)\))?\s*(:\s*$|=?\s*\{ *$|extends\s.*|with\s.*|\s*$)', bigline, re.S)
  if m:
    (ty, name, allargs, coda) = m.groups()
    argdict = {}
    # In Python class foo(bar): declarations, bar is a superclass, not
    # parameters.  If Scala the equivalent decls are parameters, just like
    # functions in both languages.
    python_style_class = ty == 'class' and coda and coda[0] == ':'
    if not python_style_class and allargs and allargs.strip():
      args = allargs.strip().split(',')
      # Strip off default assignments
      args = [x.strip().split('=')[0].strip() for x in args]
      # Strip off Scala types
      args = [x.strip().split(':')[0].strip() for x in args]
      for arg in args:
        if arg.startswith("var "):
          argdict[arg[4:].strip()] = "var"
        elif arg.startswith("val "):
          argdict[arg[4:].strip()] = "val"
        else:
          argdict[arg] = "val"
    defs += [Define(ty, name, argdict)]
    #debprint("Adding args %s for function", argdict)

  # Check for various types of blocks, and substitute.
  # We only want to check once per line, and Python
  # unfortunately makes it rather awkward to do convenient if-then checks
  # with regexps because there's no equivalent of
  #
  # if ((m = re.match(...))):
  #   do something with m.groups()
  # elif ((m = re.match(...))):
  #   etc.
  #
  # Instead you need assignment and check on separate lines, and so all
  # ways of chaining multiple regex matches will be awkward.  We choose
  # to create an infinite loop and break after each match, or at the end.
  # This almost directly mirrors the architecture of a C switch() statement.
  #
  newblock = None
  while True:
    if body:
      # Check for def
      m = re.match('def\s+(.*?)\((.*)\)$', body, re.S)
      if m:
        newblock = "def %s(%s)" % m.groups()
        break
      # Check for 'for' statement
      m = re.match('for\s+(.*?)\s+in\s+(.*)$', body, re.S)
      if m:
        newblock = "for (%s <- %s)" % m.groups()
        break
      # Check for 'if' statement
      m = re.match('if\s+(.*)$', body, re.S)
      if m:
        newblock = "if (%s)" % m.groups()
        break
      # Check for 'elif' statement
      m = re.match('elif\s+(.*)$', body, re.S)
      if m:
        newblock = "else if (%s)" % m.groups()
        break
      # Check for 'else' statement
      m = re.match('else\s*$', body, re.S)
      if m:
        newblock = "else"
        break
      # Check for 'while' statement
      m = re.match('while\s(.*)$', body, re.S)
      if m:
        newblock = "while (%s)" % m.groups()
        break
      # Check for 'try' statement
      m = re.match('try\s*$', body, re.S)
      if m:
        newblock = "try"
        break
      # Check for bare 'except' statement
      m = re.match('except\s*$', body, re.S)
      if m:
        newblock = "catch"
        break
      # Check for 'except' statement
      # FIXME: Should convert to a case statement within the body
      m = re.match('except\s+(.*)$', body, re.S)
      if m:
        newblock = "catch %s" % m.groups()
        break
      # Check for 'finally' statement
      m = re.match('finally\s*$', body, re.S)
      if m:
        newblock = "finally"
        break
      # Check for 'class(object)' statement
      # Class that inherits from `object' (new-style class), convert to
      # class without superclass
      m = re.match('class\s+(.*)\(object\)', body, re.S)
      if m:
        newblock = "class %s" % m.groups()
        break
      # Check for 'class(superclass)' statement
      m = re.match('class\s+(.*)\((.*)\)$', body, re.S)
      if m:
        newblock = "class %s extends %s" % m.groups()
        break
      # Check for 'class' statement (no superclass)
      m = re.match('class\s+([^(]*)$', body, re.S)
      if m:
        newblock = "class %s" % m.groups()
        break

    # Check for assignments and modifying assignments (e.g. +=) to variables
    # inside of functions.  Add val/var to bare assignments to variables not
    # yet seen.  Initially we add 'val', but if we later see the variable
    # being reassigned or modified, we change it to 'var'.  Also look for
    # self.* variables, but handle them differently.  For one,
    # they logically belong to the class, not the function they're in,
    # so we need to find the right dictionary to store them in.  Also,
    # we don't add 'val' or 'var' to them unless we see them in __init__(),
    # and in that case we move them outside the __init__() so they end up
    # in class scope. Existing variables at class scope get moved to
    # companion objects. (Note the following: Variables declared at class
    # scope are instance variables in Scala, but class variables in Python.
    # Instance variables in Python are set using assignments to self.*;
    # class variables in Scala are stored in a companion object.)

    #debprint("About to check for vars, line %d, fun %s",
    #    lineno, defs and defs[-1].name)
    if defs and paren_mismatch == 0:
      # Retrieve most recent def/class definition
      dd = defs[-1]
      #debprint("Checking for vars, line %d, old_bigline[%s], bigline[%s]", lineno, old_bigline, bigline)

      # We might have removed a 'self.' from a variable assignment, if
      # --remove-self was given.  But we want to know whether the assignment
      # was a self.* variable.  So we first look for an assignment in the
      # unfrobbed line, and if so, retrieve the variable name, and then
      # look at the frobbed line to get everything else (in particular,
      # the RHS, which might have been frobbed).
      assignre = re.compile('(\s*)(val\s+|var\s+|)((?:self\.|cls\.)?[a-zA-Z_][a-zA-Z_0-9]*)(\s*[+\-*/]?=)(.*)', re.S)
      m = assignre.match(old_bigline)
      if m:
        (_, _, varvar, _, _) = m.groups()
        m = assignre.match(bigline)
      if m:
        (newindent, newvaldecl, _, neweq, newrhs) = m.groups()
        #debprint("lineno: %d, Saw var: %s", lineno, varvar)
        is_self = varvar.startswith("self.") or varvar.startswith("cls.")
        # An assignment rather than a += or whatever
        is_assign = neweq.strip() == '='
        # If this a Python-style variable assignment at class level?  If so,
        # it's a class var, and we will move it to the companion object
        is_new_class_var = (not newvaldecl and is_assign and
            dd.ty == 'class' and not is_self)
        # If a class var, give it a 'cls.' prefix in the variable-assignment
        # dictionary, so we can match later refs to the var.  After this,
        # 'varvar' is the name of the var as recorded in the vardict, but
        # 'orig_varvar' is the actual name of the var in the text of the
        # program.
        orig_varvar = varvar
        if is_new_class_var:
          varvar = 'cls.' + varvar
        # Don't add var/val to a self.foo assignment unless it's in an
        # __init__() method (in which case it gets moved to class scope)
        ok_to_var_self = is_self and dd.ty == 'def' and dd.name == '__init__'
        curvardict = dd.vardict
        if is_self:
          # For a self.* variable, find the class vardict instead of the
          # vardict of the current function.
          i = len(defs) - 1
          while i > 0 and defs[i].ty != 'class':
            i -= 1
            curvardict = defs[i].vardict
        if newvaldecl:
          # The text had an explicit var/val decl (Scala-style)
          if varvar in curvardict:
            warning("Apparent redefinition of variable %s" % varvar)
          else:
            # Signal not to try and change val to var
            curvardict[varvar] = "explicit"
        else:
          # This is a Python-style variable (no declaration), or Scala-style
          # assignment to existing variable.
          #debprint("varvar: %s, curvardict: %s", varvar, curvardict)
          if varvar not in curvardict:
            if not is_assign:
              # We saw 'foo += 1' or similar, but no previous assignment
              # to 'foo'.
              warning("Apparent attempt to modify non-existent variable %s" % varvar)
            else:
              # First time we see an assignment.  Convert to a Scala
              # declaration and record the number.  We convert it to 'val',
              # but we may go back later and change to 'var'.
              curvardict[varvar] = len(lines)
              if not is_self or ok_to_var_self:
                bigline = "%sval %s%s%s%s" % (newindent, newvaldecl, orig_varvar, neweq, newrhs)
          else:
            # Variable is being reassigned, so change declaration to 'var'.
            vardefline = curvardict[varvar]
            if vardefline == "val":
              warning("Attempt to set function parameter %s" % varvar)
            elif type(vardefline) is int:
              #debprint("Subbing var for val in [%s]", lines[vardefline])
              lines[vardefline] = re.sub(r'^( *)val ', r'\1var ',
                lines[vardefline])
          if is_new_class_var:
            # Bare assignment to variable at class level, without 'var/val'.
            # This is presumably a Python-style class var, so move the
            # variable (and preceding comments) to the companion object,
            # creating one if necessary.
            if dd.compobj_lineind is None:
              # We need to create a companion object.
              lines[dd.lineind:dd.lineind] = \
                  ['%sobject %s {' % (' '*dd.indent, dd.name),
                   '%s}' % (' '*dd.indent),
                   '']
              # This should adjust dd.lineind up by 3!
              old_lineind = dd.lineind
              adjust_lineinds(dd.lineind, 3)
              assert dd.lineind == old_lineind + 3
              dd.compobj_lineind = dd.lineind - 2
            # Now move the variable assignment itself.
            inslines = bigline.split('\n')
            inspoint = dd.compobj_lineind
            lines[inspoint:inspoint] = inslines
            adjust_lineinds(inspoint, len(inslines))
            curvardict[varvar] = inspoint
            # Also move any blank or comment lines directly before.
            bcomcount = zero_mismatch_prev_blank_or_comment_line_count
            #debprint("Moving var %s, lineno=%s, bcomcount=%s",
            #    varvar, lineno, bcomcount)
            if bcomcount > 0:
              lines[inspoint:inspoint] = (
                  lines[-bcomcount:])
              adjust_lineinds(inspoint, bcomcount)
              del lines[-bcomcount:]
              adjust_lineinds(len(lines)+1, -bcomcount)

            bigline = None
        if ok_to_var_self and bigline.strip().startswith('val '):
          # If we've seen a self.* variable assignment in an __init__()
          # function, move it outside of the init statement, along with
          # any comments.
          bigline = ' '*dd.indent + bigline.lstrip()
          inslines = bigline.split('\n')
          inspoint = dd.lineind
          lines[inspoint:inspoint] = inslines
          adjust_lineinds(inspoint, len(inslines))
          if type(curvardict[varvar]) is int:
            curvardict[varvar] = inspoint
          bcomcount = zero_mismatch_prev_blank_or_comment_line_count
          if bcomcount > 0:
            # Move comments, but beforehand fix indentation
            for i in xrange(bcomcount):
              lines[-(i+1)] = re.sub(r'^( *)', ' '*dd.indent, lines[-(i+1)])
            lines[inspoint:inspoint] = (
                lines[-bcomcount:])
            adjust_lineinds(inspoint, bcomcount)
            del lines[-bcomcount:]
            adjust_lineinds(len(lines)+1, -bcomcount)
          bigline = None

    break

  # Store logical line or modified block-start line into lines[]
  if bigline is None:
    continue
  if newblock:
    startind = len(lines)
    add_bigline(front + newblock + back)
    indents += [Indent(startind, len(lines)-1, bigline_indent, "python")]
  else:
    add_bigline(bigline)
  bigline = None

# At the end, output all lines
for line in lines:
  print line

# Ignore blank line for purposes of figuring out indentation
# NOTE: No need to use \s* in these or other regexps because we call
# expandtabs() above to convert tabs to spaces, and rstrip() above to
# remove \r and \n

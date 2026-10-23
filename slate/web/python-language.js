// Python highlighting close to PyCharm's Darcula: Monaco's bundled grammar only knows comments at the start of a line
// and has no notion of def names, self, builtins, decorators or keyword arguments.
'use strict';

window.registerSlatePython = function (monaco) {
  const keywords = ['and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
    'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return',
    'try', 'while', 'with', 'yield', 'True', 'False', 'None'];
  const softKeywords = ['match', 'case', 'type'];
  const builtins = ['abs', 'aiter', 'all', 'anext', 'any', 'ascii', 'bin', 'bool', 'breakpoint', 'bytearray', 'bytes', 'callable',
    'chr', 'classmethod', 'compile', 'complex', 'delattr', 'dict', 'dir', 'divmod', 'enumerate', 'eval', 'exec', 'exit', 'filter',
    'float', 'format', 'frozenset', 'getattr', 'globals', 'hasattr', 'hash', 'help', 'hex', 'id', 'input', 'int', 'isinstance',
    'issubclass', 'iter', 'len', 'list', 'locals', 'map', 'max', 'memoryview', 'min', 'next', 'object', 'oct', 'open', 'ord', 'pow',
    'print', 'property', 'quit', 'range', 'repr', 'reversed', 'round', 'set', 'setattr', 'slice', 'sorted', 'staticmethod', 'str',
    'sum', 'super', 'tuple', 'type', 'vars', 'zip', '__import__', 'NotImplemented', 'Ellipsis', '__name__', '__file__', '__doc__',
    'BaseException', 'Exception', 'ArithmeticError', 'AssertionError', 'AttributeError', 'EOFError', 'FileNotFoundError',
    'ImportError', 'IndexError', 'KeyError', 'KeyboardInterrupt', 'LookupError', 'MemoryError', 'ModuleNotFoundError', 'NameError',
    'NotImplementedError', 'OSError', 'OverflowError', 'PermissionError', 'RecursionError', 'RuntimeError', 'StopIteration',
    'SyntaxError', 'SystemExit', 'TimeoutError', 'TypeError', 'UnicodeDecodeError', 'UnicodeEncodeError', 'ValueError',
    'ZeroDivisionError', 'Warning', 'DeprecationWarning', 'FutureWarning', 'UserWarning', 'RuntimeWarning'];

  monaco.languages.register({id: 'py', extensions: ['.py', '.pyw', '.pyi'], aliases: ['Python']});
  monaco.languages.setLanguageConfiguration('py', {
    comments: {lineComment: '#', blockComment: ['"""', '"""']},
    brackets: [['{', '}'], ['[', ']'], ['(', ')']],
    autoClosingPairs: [
      {open: '{', close: '}'}, {open: '[', close: ']'}, {open: '(', close: ')'},
      {open: '"', close: '"', notIn: ['string']}, {open: "'", close: "'", notIn: ['string', 'comment']}],
    surroundingPairs: [{open: '{', close: '}'}, {open: '[', close: ']'}, {open: '(', close: ')'}, {open: '"', close: '"'}, {open: "'", close: "'"}],
    wordPattern: /(-?\d*\.\d\w*)|([^`~!@#$%^&*()\-=+[{\]}\\|;:'",.<>/?\s]+)/g,
    onEnterRules: [{
      beforeText: /^\s*(?:def|class|for|if|elif|else|while|try|with|finally|except|async|match|case)\b.*:\s*(#.*)?$/,
      action: {indentAction: monaco.languages.IndentAction.Indent}}, {
      beforeText: /^\s*(?:return|raise|pass|break|continue)\b.*$/,
      action: {indentAction: monaco.languages.IndentAction.Outdent}}],
    folding: {offSide: true, markers: {start: /^\s*#\s*region\b/, end: /^\s*#\s*endregion\b/}},
    indentationRules: {increaseIndentPattern: /^\s*(?:def|class|for|if|elif|else|while|try|with|finally|except|async|match|case)\b.*:\s*(#.*)?$/,
      decreaseIndentPattern: /^\s*(?:elif|else|except|finally)\b.*:\s*(#.*)?$/},
  });

  // States carry the open quote and the string flags: '@str.<quote>.<flags>.<token>' with flags among n (plain), f, r, fr.
  const stringOpen = (token) => ({cases: {
    '$1~[fF][rR]|[rR][fF]': {token, next: `@str.$2.fr.${token}`},
    '$1~[fF]': {token, next: `@str.$2.f.${token}`},
    '$1~[rR][bBuU]?|[bBuU][rR]': {token, next: `@str.$2.r.${token}`},
    '@default': {token, next: `@str.$2.n.${token}`}}});

  monaco.languages.setMonarchTokensProvider('py', {
    defaultToken: '',
    tokenPostfix: '.py',
    keywords, softKeywords, builtins,
    selfs: ['self', 'cls'],
    tokenizer: {
      root: [
        {include: '@code'},
        [/[)\]}]/, 'delimiter'],
      ],
      paren: [
        [/\)/, {token: 'delimiter.paren', next: '@pop'}],
        [/[a-zA-Z_]\w*(?=\s*=(?!=))/, {cases: {'@keywords': 'keyword', '@default': 'kwarg'}}],
        {include: '@code'},
        [/[\]}]/, 'delimiter'],
      ],
      bracket: [
        [/\]/, {token: 'delimiter.bracket', next: '@pop'}],
        {include: '@code'},
        [/[)}]/, 'delimiter'],
      ],
      brace: [
        [/\}/, {token: 'delimiter.brace', next: '@pop'}],
        {include: '@code'},
        [/[)\]]/, 'delimiter'],
      ],
      code: [
        // the line-start rules come before the whitespace rule, which would otherwise eat the indentation they anchor on
        [/^(\s*)(@\s*[a-zA-Z_][\w.]*)/, ['', 'decorator']],
        [/^(\s*)([rRuU]?)("""|''')/, ['', 'string.doc', {token: 'string.doc', next: '@str.$3.n.string.doc'}]],
        [/[ \t\r\n]+/, ''],
        [/#.*$/, 'comment'],
        [/([rRbBuUfF]{0,2})("""|'''|"|')/, stringOpen('string')],
        [/(def)(\s+)(__\w+__)/, ['keyword', '', 'dunder.def']],
        [/(def)(\s+)([a-zA-Z_]\w*)/, ['keyword', '', 'function.def']],
        [/(class)(\s+)([a-zA-Z_]\w*)/, ['keyword', '', 'type.def']],
        [/0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+/, 'number'],
        [/(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?[jJ]?/, 'number'],
        [/__\w+__/, 'dunder'],
        [/[a-zA-Z_]\w*/, {cases: {
          '@keywords': 'keyword',
          '@selfs': 'self',
          '@builtins': 'builtin',
          '@softKeywords': 'identifier',
          '@default': 'identifier'}}],
        [/\(/, {token: 'delimiter.paren', next: '@paren'}],
        [/\[/, {token: 'delimiter.bracket', next: '@bracket'}],
        [/\{/, {token: 'delimiter.brace', next: '@brace'}],
        [/[,;]/, 'delimiter.comma'],
        [/\\$/, 'delimiter'],
        [/[-+*/%&|^~<>!=@:.]+/, 'operator'],
      ],
      str: [
        [/\{\{|\}\}/, {cases: {'$S3~f|fr': 'string.escape', '@default': '$S4'}}],
        [/\{/, {cases: {'$S3~f|fr': {token: 'string.fbrace', next: '@fexpr'}, '@default': '$S4'}}],
        [/\\(?:[\\'"abfnrtv0]|x[0-9a-fA-F]{2}|N\{[^}]*\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[0-7]{1,3})/,
          {cases: {'$S3~r|fr': '$S4', '@default': 'string.escape'}}],
        [/\\./, '$S4'],
        [/\\$/, '$S4'],
        [/"""|'''|"|'/, {cases: {'$#==$S2': {token: '$S4', next: '@pop'}, '@default': '$S4'}}],
        [/[^\\'"{}]+/, '$S4'],
        [/[{}]/, '$S4'],
      ],
      fexpr: [
        [/\}/, {token: 'string.fbrace', next: '@pop'}],
        [/![rsa](?=[:}])/, 'string.escape'],
        [/(:[^{}'"\n]*)(\})/, ['string', {token: 'string.fbrace', next: '@pop'}]],
        [/=(?=\s*[}!:])/, 'string.escape'],
        {include: '@code'},
      ],
    },
  });

  monaco.editor.defineTheme('darcula', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      {token: '', foreground: 'A9B7C6'},
      {token: 'keyword', foreground: 'CC7832'},
      {token: 'self', foreground: '94558D'},
      {token: 'builtin', foreground: '8888C6'},
      {token: 'function.def', foreground: 'FFC66D'},
      {token: 'dunder.def', foreground: 'B200B2'},
      {token: 'dunder', foreground: 'B200B2'},
      {token: 'type.def', foreground: 'A9B7C6'},
      {token: 'decorator', foreground: 'BBB529'},
      {token: 'kwarg', foreground: 'AA4926'},
      {token: 'number', foreground: '6897BB'},
      {token: 'string', foreground: '6A8759'},
      {token: 'string.doc', foreground: '629755', fontStyle: 'italic'},
      {token: 'string.escape', foreground: 'CC7832'},
      {token: 'string.fbrace', foreground: 'CC7832'},
      {token: 'comment', foreground: '808080', fontStyle: 'italic'},
      {token: 'delimiter.comma', foreground: 'CC7832'},
      {token: 'delimiter', foreground: 'A9B7C6'},
      {token: 'operator', foreground: 'A9B7C6'},
      // other languages Monaco ships with
      {token: 'string.key.json', foreground: '9876AA'},
      {token: 'string.value.json', foreground: '6A8759'},
      {token: 'type', foreground: 'A9B7C6'},
      {token: 'tag', foreground: 'E8BF6A'},
      {token: 'attribute.name', foreground: 'BABABA'},
      {token: 'attribute.value', foreground: 'A5C261'},
      {token: 'keyword.md', foreground: 'CC7832'},
    ],
    colors: {
      'editor.background': '#2B2B2B',
      'editor.foreground': '#A9B7C6',
      'editor.lineHighlightBackground': '#323232',
      'editor.lineHighlightBorder': '#00000000',
      'editorLineNumber.foreground': '#606366',
      'editorLineNumber.activeForeground': '#A4A3A3',
      'editorGutter.background': '#313335',
      'editor.selectionBackground': '#214283',
      'editor.inactiveSelectionBackground': '#21428388',
      'editor.wordHighlightBackground': '#344134',
      'editor.wordHighlightStrongBackground': '#40332B',
      'editor.findMatchBackground': '#32593D',
      'editor.findMatchHighlightBackground': '#32593D99',
      'editorCursor.foreground': '#BBBBBB',
      'editorIndentGuide.background1': '#393939',
      'editorIndentGuide.activeBackground1': '#505050',
      'editorWhitespace.foreground': '#404040',
      'editorBracketMatch.background': '#3B514D',
      'editorBracketMatch.border': '#00000000',
      'editorWarning.foreground': '#BE9117',
      'editorError.foreground': '#FF6B68',
      'editorWidget.background': '#3C3F41',
      'editorWidget.border': '#555555',
      'editorSuggestWidget.background': '#3C3F41',
      'editorSuggestWidget.selectedBackground': '#0D293E',
      'editorHoverWidget.background': '#3C3F41',
      'scrollbarSlider.background': '#5A5D5F66',
      'scrollbarSlider.hoverBackground': '#5A5D5F99',
      'scrollbarSlider.activeBackground': '#5A5D5FCC',
      'editorOverviewRuler.border': '#00000000',
      'peekViewEditor.background': '#2B2B2B',
      'peekViewResult.background': '#3C3F41',
      'peekViewTitle.background': '#3C3F41',
    },
  });
};

# App-specific R8 rules. Library rules (OkHttp, Compose, AndroidX) ship with the
# libraries themselves as consumer rules and do not need repeating here.

# Keep source file names and line numbers so release stack traces stay readable.
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

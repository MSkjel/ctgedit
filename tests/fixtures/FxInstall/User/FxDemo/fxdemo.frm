! the genie forms of fxdemo.ctm
FORM "tank", "Genie - Demo tank";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=REAL";
2: "Label", 16;
NORMAL
"Level  {1                 }
Label  {2          }"

FORM "pump", "Genie - Demo pump";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
2: "Label", 16;
NORMAL
"Running  {1                 }
Label    {2          }"

FORM "valve", "Genie - Demo valve";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
2: "Label", 16;
NORMAL
"Open   {1                 }
Label  {2          }"

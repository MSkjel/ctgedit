! the genie forms of fxparts.ctm (our own forms)
! a second comment line before the first form
FORM "fx_named", "Genie - Fx named";
1: "Name", 40;
2: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
3: "Spare", 16;
NORMAL
"Name {1                     }
{LINE, 40}
Tag  {2                 }"

FORM "fx_insert_object", "Genie - Insert Object";
1: "GUID", 40;
2: "EVENTCLASS", 32;
3: "W", 8;
4: "H", 8;
5: "AN", 8, readonly;
NORMAL
"GUID        {1                     }
Event class {2                 }
Width       {3     }
Height      {4     }
AN          {5     }"

FORM "fx_listy", "Genie - Fx lists";
1: "Mode", 24, readwrite, "Labels", "Name", "FX_MODE_*";
2: "Flag", 6, readwrite, "FxBoolean", "NAME";
3: "Tag", 32, readwrite, "Variable Tags", "NAME";
NORMAL
"Mode {1             }
{LINE, 30}
Flag {2   }
{LINE, 30}
Tag  {3                 }"

FORM "fx_listy", "Genie - Fx lists (the later block)";
1: "Mode", 24;
NORMAL
"Mode {1             }"

FORM "fx_broken", "Genie - a form numbered 4 to 11";
4: "Tag", 16;
5: "Label", 16;
6: "A", 8;
7: "B", 8;
8: "C", 8;
9: "D", 8;
10: "E", 8;
11: "F", 8;
NORMAL
"Tag {4          }
Label {5          }"

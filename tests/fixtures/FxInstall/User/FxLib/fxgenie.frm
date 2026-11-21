! the genie forms of fxgenie.ctm
FORM "fx_pump", "Genie - Fx pump";
1: "Tag", 32, readwrite, "Variable Tags", "NAME", "TYPE=DIGITAL";
2: "Label", 16;
NORMAL
"Tag    {1                 }
{LINE, 20}
Label  {2          }"

FORM "fx_pane", "Genie - Fx pane";
1: "Name", 40;
2: "DefaultPage", 32;
3: "IsDefault", 6, readwrite, "FxBoolean", "NAME";
4: "HasScrollbars", 6, readwrite, "FxBoolean", "NAME";
NORMAL
"Name           {1                          }
Default page   {2                 }
{LINE, 30}
Is default     {3   }   Scroll bars {4   }"

! the pane form of fxws.ctm (a workspace pane of our own)
FORM "pane", "Genie - Fx pane";
1: "Name", 64;
2: "DefaultPage", 32, readwrite, "Pages", "NAME";
3: "IsDefault", 6, readwrite, "FxBoolean", "NAME";
4: "HasScrollbars", 6, readwrite, "FxBoolean", "NAME";
5: "DisplayMode", 32, readwrite, "Labels", "Name", "PANE_DISPLAYMODE_*";
6: "FillMode", 32, readwrite, "Labels", "Name", "PANE_FILLMODE_*";
7: "ContentTypes", 64, readwrite, "Contype", "NAME";
8: "TabPaneName", 32;
9: "TabControlName", 32;
10: "ExcludedAutofillPanes", 64;
11: "EquipRefAssocs", 64;
NORMAL
"Name                 {1                               }
Default page         {2                 }
{LINE, 60}
Is default           {3   }   Scroll bars {4   }
Display mode         {5                 }
Fill mode            {6                 }
{LINE, 60}
Content types        {7                               }
Tab pane             {8                 }
Tab control          {9                 }
Excluded panes       {10                              }
Categories           {11                              }"

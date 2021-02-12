# menus
 menu system built by bot
 user can create items that are arranged as a multi level menu
 embeds and message links will be used to allow clicking forward and back
 the actual messages will be stored in a yaml file that user can upload and easily edit
 user logic is: user uploads a file ($menuupload). that file is saved in bot directory (overwriting existing one) and read when bot is restarted for any reason
 file format is basically a sequnce of entries
 - entry: code (code is in format 1-2-3
   title: title
   contents: contents (in discord format.) later we can add more fields for more complex embeds, etc

special format: [](&<code>&) means put in a link to code

example:```
- entry: 1-2
  title: "a first entry v2"
  contents: "**exciting default** contents\n goto [other entry](&<1-3>&)"
- entry: 1-3
  title: "another *default entry*"
  contents: "this is like wow\n [back](&<1-2>&)"
- entry: 123
  title: "another *entry*"
  contents: "this is like wow\n [back](&<1-3>&)"
- entry: 125
  title: "yet another *entry*"
  contents: "meh\n [back](&<123>&)"
```
program logic: scan once to create tree, then create messages in predefined channel (that others cannot do anything to) with the embeded special format, then update the codes IN THE MESSAGES with correct message links


for now will use same token as project_ui and the two may be later combined

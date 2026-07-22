import clr
import re
import textwrap
from pyrevit import revit, DB, UI, forms

# Grab current document and UI document
doc = revit.doc
uidoc = revit.uidoc

class FormatSpecWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)
        self.is_formatting = False
        self.final_width = 5.5
        self.char_limit = 85
        self.final_text = ""
        
    def format_and_place(self, sender, args):
        width_str = self.WidthInput.Text
        char_str = self.CharInput.Text
        raw_text = self.SpecTextInput.Text
        
        # 1. Validate Inputs
        try:
            self.final_width = float(width_str)
            self.char_limit = int(char_str)
        except ValueError:
            forms.alert("Please enter valid numeric values for Width and Character Count.", title="Input Error")
            return
            
        if not raw_text.strip() or raw_text.startswith("Paste your"):
            forms.alert("Please paste some text to format.", title="Input Error")
            return

        # 2. Save state and CLOSE window
        self.final_text = raw_text
        self.is_formatting = True
        self.Close()

# Launch the tool
window = FormatSpecWindow('window.xaml')
window.ShowDialog()

# --- BACK IN THE MAIN REVIT THREAD ---

if window.is_formatting:
    width_ft = window.final_width / 12.0
    char_limit = window.char_limit
    
    # --- PRE-PROCESSING PASS 1: SOFT INDENTS ---
    # Convert multiple spaces (2+) followed by a list prefix into a hard line break.
    cleaned_source = re.sub(r'[ \t]{2,}(?=\d+(\.\d+)+\s+|\d+\.\s+|[A-Z]\.\s+|[a-z]\.\s+)', '\n', window.final_text)
    
    # --- PRE-PROCESSING PASS 2: ERRANT LINE BREAKS ---
    # Reassemble paragraphs that were broken manually mid-sentence
    raw_lines = cleaned_source.splitlines()
    logical_lines = []
    
    for line in raw_lines:
        line = line.strip()
        
        if not line:
            logical_lines.append("")
            continue
            
        # Check if the line starts with a valid CSI break condition
        is_section = re.match(r'^(\*?\s*(CSI\s+)?SECTION)', line, re.IGNORECASE)
        is_part = re.match(r'^PART', line, re.IGNORECASE)
        # Matches: A. | 1. | a. | 1.1 (and 1.1.1 etc)
        is_list = re.match(r'^([A-Z]\.\s+|\d+\.\s+|[a-z]\.\s+|\d+(\.\d+)+\s+)', line)
        
        if is_section or is_part or is_list or not logical_lines:
            # It's a valid new paragraph block
            logical_lines.append(line)
        else:
            # It's an errant break without a prefix. Glue it to the previous line.
            if logical_lines[-1] == "":
                logical_lines.append(line)
            else:
                logical_lines[-1] += " " + line
                # Collapse any double spaces created by the glue
                logical_lines[-1] = re.sub(r'[ \t]{2,}', ' ', logical_lines[-1])
    
    # --- MAIN FORMATTING PASS ---
    clean_lines = []
    formats = []
    current_idx = 0
    
    # We now loop through our perfectly reassembled logical blocks
    for line in logical_lines:
        
        if not line:
            clean_lines.append("")
            current_idx += 1 
            continue
            
        # Regex Patterns - Separate prefix from content
        match_upper = re.match(r'^([A-Z]\.\s+)(.*)', line)
        match_num = re.match(r'^(\d+\.\s+)(.*)', line)
        match_lower = re.match(r'^([a-z]\.\s+)(.*)', line)
        
        # Only SECTION gets bolded/underlined (PART is ignored for formatting)
        match_header = re.match(r'^(\*?\s*(CSI\s+)?SECTION.*)', line, re.IGNORECASE)
        
        fmt_type = None
        
        # Determine indents and prefixes
        if match_upper:
            tabs = "\t"
            prefix = match_upper.group(1)
            content = match_upper.group(2)
        elif match_num:
            tabs = "\t\t"
            prefix = match_num.group(1)
            content = match_num.group(2)
        elif match_lower:
            tabs = "\t\t\t"
            prefix = match_lower.group(1)
            content = match_lower.group(2)
        else:
            tabs = ""
            prefix = ""
            content = line
            if match_header:
                fmt_type = "Header"
                
        # Apply Python TextWrap
        if content or prefix:
            initial_indent = tabs + prefix
            # Generate blank spaces equal to the prefix length to pad the wrapped lines
            subsequent_indent = tabs + (" " * len(prefix))
            
            # Collapse any remaining mid-sentence double spaces for a clean wrap
            content = re.sub(r'[ \t]{2,}', ' ', content)
            
            if fmt_type == "Header":
                wrapped_text = textwrap.fill(content, width=char_limit)
            elif prefix:
                wrapped_text = textwrap.fill(content, width=char_limit, 
                                             initial_indent=initial_indent, 
                                             subsequent_indent=subsequent_indent)
            else:
                wrapped_text = textwrap.fill(content, width=char_limit)
        else:
            wrapped_text = ""

        # Swap Python Newlines for Revit Carriage Returns
        wrapped_text = wrapped_text.replace('\n', '\r')
            
        length = len(wrapped_text)
        clean_lines.append(wrapped_text)
        
        # Record where bold/underline formatting should be applied
        if fmt_type:
            formats.append({"type": fmt_type, "start": current_idx, "length": length})
            
        # Advance index (+1 for the \r separating main paragraphs)
        current_idx += length + 1
        
    # Join the fully wrapped paragraphs
    final_string = "\r".join(clean_lines)
    
    # Create Revit FormattedText Object
    fmt_text = DB.FormattedText(final_string)
    
    for fmt in formats:
        if fmt["type"] == "Header":
            rng = DB.TextRange(fmt["start"], fmt["length"])
            fmt_text.SetBoldStatus(rng, True)
            fmt_text.SetUnderlineStatus(rng, True)
            
    # Prompt for Placement
    try:
        pt = uidoc.Selection.PickPoint("Click on the sheet to place the top-left corner of the text box.")
    except Exception:
        pass
    else:
        # Get default text note type
        default_type_id = doc.GetDefaultElementTypeId(DB.ElementTypeGroup.TextNoteType)
        opts = DB.TextNoteOptions(default_type_id)
        opts.HorizontalAlignment = DB.HorizontalTextAlignment.Left
        
        # Execute Revit Transaction
        with revit.Transaction("Format and Place Spec (Hard-Wrapped)"):
            text_note = DB.TextNote.Create(doc, doc.ActiveView.Id, pt, width_ft, final_string, opts)
            text_note.SetFormattedText(fmt_text)
import re

with open('panel.html', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Check if manual_card_number already exists
if 'manual_card_number' in content:
    print('manual_card_number already exists')
else:
    # Add card_number field after phone field
    pattern = r'(<input type="tel" id="manual_phone"[^>]*>)'
    replacement = r'''\1
                    </div>
                    <div class="form-group" style="flex:1 1 220px;">
                        <label>Номер карты (для реквизитов)</label>
                        <input type="text" id="manual_card_number" placeholder="2200******1234">'''
    new_content = re.sub(pattern, replacement, content)
    
    with open('panel.html', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('manual_card_number field added')

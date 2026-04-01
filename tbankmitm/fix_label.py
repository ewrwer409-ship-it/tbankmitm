import re

with open('panel.html', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Fix broken label
content = content.replace('<label>  ( )</label>', '<label>Номер карты (для реквизитов)</label>')

# Also check if we need to add phone field back if it was lost
if 'id="manual_phone"' not in content:
    print('WARNING: manual_phone field is missing!')
    # Add phone field before card_number
    content = content.replace(
        '<label>Номер карты (для реквизитов)</label>',
        '<label>Телефон (для СБП)</label>\n                        <input type="tel" id="manual_phone" placeholder="+79991234567">\n                    </div>\n                    <div class="form-group" style="flex:1 1 220px;">\n                        <label>Номер карты (для реквизитов)</label>'
    )
    print('Added manual_phone field')

with open('panel.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('Fixed panel.html')

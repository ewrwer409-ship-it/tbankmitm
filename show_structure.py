with open('panel.html', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

# Find form-row sections in manual card
in_manual = False
for i, line in enumerate(lines):
    if 'manual-card' in line:
        in_manual = True
    if in_manual:
        print(f"{i+1}: {line.rstrip()[:100]}")
        if i > 600:  # Stop after reasonable amount
            break

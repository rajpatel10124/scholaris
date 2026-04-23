import os
import sys

# Target file
target = 'app.py'

if not os.path.exists(target):
    print(f"File {target} not found")
    sys.exit(1)

content = open(target, 'r').read()
old_line = 'app.run(debug=True, use_reloader=False)'
new_line = "socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False)"

if old_line in content:
    new_content = content.replace(old_line, new_line)
    with open(target, 'w') as f:
        f.write(new_content)
    print("SUCCESS: Runner fixed")
else:
    # Try with different indentation or characters
    import re
    new_content, count = re.subn(r'app\.run\(.*?\)', new_line, content)
    if count > 0:
        with open(target, 'w') as f:
            f.write(new_content)
        print(f"SUCCESS: Fixed {count} occurrences via regex")
    else:
        print("ERROR: app.run not found")

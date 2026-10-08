import os
from tools.browser import BrowserTool

def test():
    print("Testing Autofill Feature...")
    tool = BrowserTool()
    
    html_path = os.path.abspath("test_form.html").replace('\\', '/')
    url = f"file:///{html_path}"
    
    print(f"1. Navigating to {url}")
    nav_res = tool.run({"action": "navigate", "target": url})
    print(f"Navigate Result: {nav_res}")
    
    import time
    time.sleep(2)
    
    print("2. Running Autofill")
    fill_res = tool.run({"action": "autofill"})
    print(f"Autofill Result: {fill_res}")
    
if __name__ == "__main__":
    test()

import json
import logging
from pathlib import Path
from playwright.sync_api import Page, ElementHandle

logger = logging.getLogger("nida.browser.autofill")

class AutofillerController:
    """
    Scrapes the active Playwright page for input fields,
    analyzes their labels/names, and fills them natively
    using data from autofill.json.
    """

    def __init__(self, page: Page):
        self.page = page
        self.data_path = Path(__file__).parent.parent.parent / "autofill.json"

    def _load_data(self) -> dict:
        if not self.data_path.exists():
            return {}
        try:
            return json.loads(self.data_path.read_text("utf-8"))
        except Exception as e:
            logger.error(f"Error reading autofill.json: {e}")
            return {}

    def fill_form(self) -> dict:
        data = self._load_data()
        if not data:
            return {"ok": False, "feedback": "Your autofill.json file is missing or empty."}

        # 1. Inject JavaScript to extract all input metadata and tag them with selectors
        extractor_js = """
        () => {
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]), textarea, select'));
            return inputs.map((el, index) => {
                if (!el.id && !el.dataset.nidaAutofill) {
                    el.dataset.nidaAutofill = 'nida-fid-' + index;
                }
                let labelText = '';
                if (el.labels && el.labels.length > 0) {
                    labelText = el.labels[0].innerText;
                } else if (el.id) {
                    const label = document.querySelector(`label[for="${el.id}"]`);
                    if (label) labelText = label.innerText;
                }
                
                // Get closest previous text node or preceding label as fallback
                if (!labelText) {
                    const prev = el.previousElementSibling;
                    if (prev && prev.innerText) {
                        labelText = prev.innerText;
                    }
                }

                return {
                    tagName: el.tagName.toLowerCase(),
                    type: el.type || '',
                    name: el.name || '',
                    id: el.id || '',
                    nidaId: el.dataset.nidaAutofill || '',
                    placeholder: el.placeholder || '',
                    label: labelText,
                    selector: el.id ? `#${el.id}` : `[data-nida-autofill="${el.dataset.nidaAutofill}"]`
                };
            });
        }
        """

        try:
            elements = self.page.evaluate(extractor_js)
        except Exception as e:
            logger.error(f"Failed to evaluate extraction JS: {e}")
            return {"ok": False, "feedback": "Failed to read the form on this page."}

        filled_count = 0
        
        # 2. Heuristic Matching
        synonyms = {
            "fullName": ["name", "fullname", "first name", "last name", "full name"],
            "firstName": ["firstname", "first name", "fname", "given name"],
            "lastName": ["lastname", "last name", "lname", "surname"],
            "email": ["email", "e-mail", "mailid", "mail id", "email address", "email id"],
            "phone": ["phone", "mobile", "cell", "contact", "number", "tel", "telephone"],
            "address": ["address", "street", "location", "residence"],
            "city": ["city", "town", "district"],
            "zipcode": ["zip", "postal", "pincode", "pin code", "zipcode"],
            "company": ["company", "organization", "employer", "business"],
            "username": ["user name", "username", "login id", "loginid"],
            "password": ["password", "pass", "pwd"],
            "gender": ["gender", "sex"],
            "birthday": ["birthday", "dob", "date of birth", "birth date", "birthdate"]
        }

        for el in elements:
            # Create a combined clue string (e.g. "email emailAddress  Enter your email")
            clue_string = f"{el['name']} {el['id']} {el['placeholder']} {el['label']}".lower()
            clue_clean = clue_string.replace(" ", "").replace("_", "").replace("-", "")
            
            if not clue_clean:
                continue

            best_match_value = None
            
            for key, value in data.items():
                # 1. Direct key match
                k_clean = key.lower().replace(" ", "").replace("_", "").replace("-", "")
                if k_clean in clue_clean or (len(clue_clean) > 3 and clue_clean in k_clean):
                    best_match_value = value
                    break
                    
                # 2. Synonym match
                matched_synonym = False
                if key in synonyms:
                    for syn in synonyms[key]:
                        syn_clean = syn.replace(" ", "")
                        if syn_clean in clue_clean:
                            best_match_value = value
                            matched_synonym = True
                            break
                if matched_synonym:
                    break
                    
                    
            if best_match_value and el['selector']:
                try:
                    loc = self.page.locator(el['selector']).first
                    if loc.count() > 0:
                        # Ensure we don't accidentally overwrite fields that already have data
                        # unless they're default values.
                        current_val = loc.input_value()
                        if current_val.strip() == "":
                            if el['tagName'] == 'select':
                                # Playwright selects by value, label, or index
                                loc.select_option(label=str(best_match_value))
                            else:
                                loc.fill(str(best_match_value))
                            
                            # Dispatch standard events for reactive setups (React, Vue, etc.)
                            try:
                                loc.dispatch_event('input')
                                loc.dispatch_event('change')
                            except Exception:
                                pass
                                
                            filled_count += 1
                except Exception as e:
                    logger.debug(f"Could not fill selector {el['selector']}: {e}")

        if filled_count > 0:
            return {"ok": True, "feedback": f"Auto-filled {filled_count} fields."}
        else:
            return {"ok": True, "feedback": "Could not identify any matching form fields."}

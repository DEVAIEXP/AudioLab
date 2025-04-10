from gradio.components.base import Component
import logging

logger = logging.getLogger(__name__)


class ArgHandler:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ArgHandler, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "args"):
            self.args = {}
        if not hasattr(self, "descriptions"):
            self.descriptions = {}
        if not hasattr(self, "elements"):
            self.elements = {}  # Dictionary to track registered elements

    def register_description(self, wrapper_name: str, elem_name: str, description: str):
        elem_id = f"{wrapper_name}_{elem_name}"
        self.descriptions[elem_id] = description

    def register_element(self, wrapper_name: str, elem_name: str, gradio_element: Component, description: str = None):
        # Initialize wrapper key in the dictionaries
        if wrapper_name not in self.args:
            self.args[wrapper_name] = {}
        if wrapper_name not in self.elements:
            self.elements[wrapper_name] = {}

        # Get initial value (if available)
        element_value = getattr(gradio_element, "value", None)
        self.args[wrapper_name][elem_name] = element_value
        self.elements[wrapper_name][elem_name] = gradio_element

        # Optionally register description
        if description:
            self.register_description(wrapper_name, elem_name, description)

        # Set listeners for the element
        for method in ["upload", "change", "clear"]:
            if hasattr(gradio_element, method):
                getattr(gradio_element, method)(
                    lambda value, wn=wrapper_name, en=elem_name: self.update_element(wn, en, value),
                    inputs=gradio_element,
                    show_progress="hidden"
                )

    def update_element(self, wrapper_name: str, elem_name: str, value):
        # Dynamically update the dictionary with new values
        if wrapper_name in self.args and elem_name in self.args[wrapper_name]:
            self.args[wrapper_name][elem_name] = value
            logger.info(f"Updated {wrapper_name}.{elem_name} -> {value}")

    def get_element(self, wrapper_name: str, elem_name: str):
        # Retrieve the actual Gradio element
        return self.elements.get(wrapper_name, {}).get(elem_name, None)

    def get_args(self):
        return self.args

    def get_descriptions_js(self):
        return """
    console.log("[DEBUG] Script injected...");
    let hintsSet = false;

    function setDescriptions() {
      if (typeof gradioApp === "undefined" || !gradioApp()) {
        console.warn("[DEBUG] gradioApp() not defined or returned null.");
        return;
      }

      let hintItems = gradioApp().querySelectorAll(".hintitem");
      
      if (!hintItems || hintItems.length === 0) {
        console.log("[DEBUG] .hintitem elements not found.");
        return;
      }

      // Build the descriptions object
      const descriptions = {
        """ + ", ".join([f'"{k}": "{v}"' for k, v in self.descriptions.items()]) + """
      };
      console.log("[DEBUG] descriptions:", descriptions);
      const processorList = gradioApp().querySelector("#processor_list");
      // Get all of the label elements in the processor list
      const processorLabels = processorList.querySelectorAll("label");
        // Go through each label element
        for (let label of processorLabels) {
            // Get the label value
            let labelValue = label.innerText + "_description";
            let description = descriptions[labelValue];
            if (description) {
                label.title = description;
            }
        }

      // Go through each .hintitem
      for (let hintItem of hintItems) {
        let elemId = hintItem.id;
        let description = descriptions[elemId];
        addHintButton(hintItem, description);
        
        if (description) {
          hintItem.title = description;
          let inputs = hintItem.getElementsByTagName("input");
          let labels = hintItem.getElementsByTagName("label");
          for (let input of inputs) {
            input.title = description;
          }
          for (let label of labels) {
            label.title = description;
          }
        }
      }
    }

    function addHintButton(hintItem, description) {
      let container = hintItem.getElementsByClassName("container")[0];
      let wrap = hintItem.getElementsByClassName("wrap")[0];
      if (!container && !wrap) {        
        console.warn("[DEBUG] No container with class 'container' or 'wrap' found.");
        return;    
      }

      // Create the hint button
      let hintButton = document.createElement("button");
      hintButton.className = "hintButton";
      hintButton.innerText = "?";

      // Set the title to the description
      hintButton.title = description;
      
      let head = hintItem.getElementsByClassName("head")[0];
      if (head) {
        hintButton.className = "hintButton head";
      }

      // Append the button to the container
      if (container) {
          container.appendChild(hintButton);
      } else {
            hintItem.appendChild(hintButton);
        }
    }

    function waitForGradioApp() {
      console.log("[DEBUG] Waiting for gradioApp...");
      refresh();
      const interval = setInterval(() => {
        if (typeof gradioApp !== "undefined" && gradioApp()) {        
          console.log("[DEBUG] gradioApp() loaded. Initializing setDescriptions and addHintButton...");
          setDescriptions();
          clearInterval(interval);
          hintsSet = true;
        } else {
          console.log("[DEBUG] gradioApp() not ready. Retrying...");
        }
      }, 1000); // Retry every 1 second
    }

    onUiLoaded(function () {        
      if (!hintsSet) {
        console.log("[DEBUG] UI loaded. Starting waitForGradioApp...");
        waitForGradioApp();
      }
    });

    if (document.readyState === "complete" || document.readyState === "interactive") {
      console.log("[DEBUG] DOM already loaded. Starting waitForGradioApp...");
      waitForGradioApp();
    }
    """

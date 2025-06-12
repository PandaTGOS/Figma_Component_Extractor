import os
import json
import asyncio
from typing import Dict, List, Optional, Any, Tuple
import httpx
from dataclasses import dataclass
import math
from dotenv import load_dotenv

load_dotenv()

@dataclass
class FigmaColor:
    r: float
    g: float
    b: float
    a: float = 1.0

    def to_css(self) -> str:
        if self.a < 1.0:
            return f"rgba({int(self.r*255)}, {int(self.g*255)}, {int(self.b*255)}, {round(self.a, 2)})"
        return f"#{int(self.r*255):02x}{int(self.g*255):02x}{int(self.b*255):02x}"

class FigmaComponentExtractor:
    def __init__(self, personal_access_token: str, file_key: str):
        self.base_url = "https://api.figma.com/v1"
        self.headers = {"X-FIGMA-TOKEN": personal_access_token}
        self.file_key = file_key
        self.output_dir = "figma_components"
        self.timeout = httpx.Timeout(30.0, connect=60.0)
        os.makedirs(self.output_dir, exist_ok=True)

    async def fetch_entire_file(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Fetch complete file data including nodes, styles, and components"""
        url = f"{self.base_url}/files/{self.file_key}"
        params = {
            "depth": 10,  # Get deep nesting
            "geometry": "paths",  # Include vector path data
            "plugin_data": "shared"  # Get any plugin data
        }
        response = await client.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        return response.json()

    async def fetch_component_image(self, client: httpx.AsyncClient, node_id: str) -> Optional[bytes]:
        """Fetch component preview image"""
        url = f"{self.base_url}/images/{self.file_key}?ids={node_id}&format=svg"  # SVG for better quality
        response = await client.get(url, headers=self.headers)
        image_url = response.json().get("images", {}).get(node_id)
        if image_url:
            img_response = await client.get(image_url)
            return img_response.content
        return None

    def extract_component_data(self, node: Dict[str, Any], parent: Dict[str, Any] = None) -> Dict[str, Any]:
        """Extract comprehensive component data including internal structure"""
        component = {
            "id": node.get("id"),
            "name": node.get("name"),
            "type": node.get("type").lower(),
            "visible": node.get("visible", True),
            "locked": node.get("locked", False),
            "parent": self._extract_parent_info(parent),
            "layout": self._extract_layout(node),
            "style": self._extract_style(node),
            "children": [],
            "css": {},
            "html": {},
            "metadata": {
                "createdAt": node.get("createdAt"),
                "updatedAt": node.get("updatedAt"),
                "figmaUrl": f"https://www.figma.com/file/{self.file_key}/?node-id={node.get('id')}"
            }
        }

        # Process children recursively
        if "children" in node:
            for child in node["children"]:
                component["children"].append(self.extract_component_data(child, node))

        # Generate CSS and HTML representations
        component["css"] = self._generate_css(component)
        component["html"] = self._generate_html(component)

        return component

    def _extract_parent_info(self, parent: Dict[str, Any]) -> Dict[str, Any]:
        if not parent:
            return {}
        return {
            "id": parent.get("id"),
            "name": parent.get("name"),
            "type": parent.get("type").lower()
        }

    def _extract_layout(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract all layout-related properties"""
        layout = {}
        if "absoluteBoundingBox" in node:
            bbox = node["absoluteBoundingBox"]
            layout.update({
                "x": bbox.get("x"),
                "y": bbox.get("y"),
                "width": bbox.get("width"),
                "height": bbox.get("height"),
            })

        if "relativeTransform" in node:
            layout["transform"] = node["relativeTransform"]

        if "constraints" in node:
            layout["constraints"] = {
                "horizontal": node["constraints"].get("horizontal"),
                "vertical": node["constraints"].get("vertical")
            }

        if "layoutMode" in node:
            layout["flex"] = {
                "direction": node.get("layoutMode"),
                "padding": {
                    "top": node.get("paddingTop"),
                    "right": node.get("paddingRight"),
                    "bottom": node.get("paddingBottom"),
                    "left": node.get("paddingLeft")
                },
                "itemSpacing": node.get("itemSpacing"),
                "alignItems": node.get("primaryAxisAlignItems"),
                "justifyContent": node.get("counterAxisAlignItems")
            }

        if "cornerRadius" in node:
            if isinstance(node["cornerRadius"], (int, float)):
                layout["borderRadius"] = node["cornerRadius"]
            elif isinstance(node["cornerRadius"], dict):
                layout["borderRadius"] = {
                    "topLeft": node["cornerRadius"].get("topLeft"),
                    "topRight": node["cornerRadius"].get("topRight"),
                    "bottomRight": node["cornerRadius"].get("bottomRight"),
                    "bottomLeft": node["cornerRadius"].get("bottomLeft")
                }

        return layout

    def _extract_style(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract all style-related properties"""
        style = {}

        # Fills (background)
        if "fills" in node:
            style["fills"] = []
            for fill in node["fills"]:
                if fill.get("visible", True):
                    fill_data = {
                        "type": fill.get("type"),
                        "blendMode": fill.get("blendMode"),
                        "opacity": fill.get("opacity", 1.0)
                    }
                    if fill.get("color"):
                        fill_data["color"] = self._extract_color(fill["color"])
                    if fill.get("gradientStops"):
                        fill_data["gradient"] = [self._extract_color(stop["color"]) for stop in fill["gradientStops"]]
                    style["fills"].append(fill_data)

        # Strokes (borders)
        if "strokes" in node:
            style["strokes"] = []
            for stroke in node["strokes"]:
                if stroke.get("visible", True):
                    stroke_data = {
                        "type": stroke.get("type"),
                        "blendMode": stroke.get("blendMode"),
                        "weight": node.get("strokeWeight", 1),
                        "position": node.get("strokeAlign", "INSIDE")
                    }
                    if stroke.get("color"):
                        stroke_data["color"] = self._extract_color(stroke["color"])
                    style["strokes"].append(stroke_data)

        # Effects (shadows, blurs)
        if "effects" in node:
            style["effects"] = []
            for effect in node["effects"]:
                if effect.get("visible", True):
                    effect_data = {
                        "type": effect.get("type"),
                        "blendMode": effect.get("blendMode"),
                        "radius": effect.get("radius", 0)
                    }
                    if effect.get("color"):
                        effect_data["color"] = self._extract_color(effect["color"])
                    if effect.get("offset"):
                        effect_data["offset"] = effect["offset"]
                    style["effects"].append(effect_data)

        # Text styles
        if node.get("type") == "TEXT":
            style["text"] = {
                "content": node.get("characters", ""),
                "font": {
                    "family": node.get("style", {}).get("fontFamily"),
                    "weight": node.get("style", {}).get("fontWeight"),
                    "size": node.get("style", {}).get("fontSize"),
                    "lineHeight": node.get("style", {}).get("lineHeightPx"),
                    "letterSpacing": node.get("style", {}).get("letterSpacing"),
                    "textCase": node.get("style", {}).get("textCase"),
                    "textDecoration": node.get("style", {}).get("textDecoration")
                },
                "align": {
                    "horizontal": node.get("textAlignHorizontal"),
                    "vertical": node.get("textAlignVertical")
                }
            }

        # Corner radius (legacy)
        if "cornerRadius" in node and isinstance(node["cornerRadius"], (int, float)):
            style["borderRadius"] = node["cornerRadius"]

        return style

    def _extract_color(self, color_data: Dict[str, float]) -> Dict[str, Any]:
        """Convert Figma color to structured format"""
        return {
            "r": color_data.get("r"),
            "g": color_data.get("g"),
            "b": color_data.get("b"),
            "a": color_data.get("a", 1.0),
            "css": FigmaColor(
                color_data.get("r", 0),
                color_data.get("g", 0),
                color_data.get("b", 0),
                color_data.get("a", 1.0)
            ).to_css()
        }

    def _generate_css(self, component: Dict[str, Any]) -> Dict[str, Any]:
        """Generate CSS properties from Figma data"""
        css = {
            "selector": f".{component['name'].lower().replace(' ', '-')}",
            "properties": {},
            "states": {}
        }

        # Layout properties
        if "layout" in component:
            layout = component["layout"]
            if "width" in layout:
                css["properties"]["width"] = f"{layout['width']}px"
            if "height" in layout:
                css["properties"]["height"] = f"{layout['height']}px"
            if "borderRadius" in layout:
                if isinstance(layout["borderRadius"], dict):
                    css["properties"]["border-radius"] = \
                        f"{layout['borderRadius']['topLeft']}px " \
                        f"{layout['borderRadius']['topRight']}px " \
                        f"{layout['borderRadius']['bottomRight']}px " \
                        f"{layout['borderRadius']['bottomLeft']}px"
                else:
                    css["properties"]["border-radius"] = f"{layout['borderRadius']}px"

        # Style properties
        if "style" in component:
            style = component["style"]
            
            # Background
            if "fills" in style and style["fills"]:
                fill = style["fills"][0]  # Use first fill
                if fill["type"] == "SOLID" and "color" in fill:
                    css["properties"]["background-color"] = fill["color"]["css"]
            
            # Borders
            if "strokes" in style and style["strokes"]:
                stroke = style["strokes"][0]  # Use first stroke
                if "color" in stroke:
                    css["properties"]["border"] = \
                        f"{stroke['weight']}px solid {stroke['color']['css']}"
            
            # Text
            if "text" in style:
                text = style["text"]
                if "font" in text:
                    font = text["font"]
                    css["properties"].update({
                        "font-family": f"'{font['family']}', sans-serif",
                        "font-weight": font["weight"],
                        "font-size": f"{font['size']}px",
                        "line-height": f"{font['lineHeight']}px",
                        "letter-spacing": f"{font['letterSpacing']}px",
                        "text-align": text["align"]["horizontal"].lower(),
                    })
            
            # Effects
            if "effects" in style and style["effects"]:
                for effect in style["effects"]:
                    if effect["type"] == "DROP_SHADOW":
                        offset = effect.get("offset", {"x": 0, "y": 0})
                        css["properties"]["box-shadow"] = \
                            f"{offset['x']}px {offset['y']}px {effect['radius']}px {effect['color']['css']}"

        return css

    def _generate_html(self, component: Dict[str, Any]) -> Dict[str, Any]:
        """Generate HTML structure from component data"""
        html = {
            "tag": self._determine_html_tag(component),
            "attributes": {},
            "content": "",
            "children": []
        }

        # Set common attributes
        if "id" in component:
            html["attributes"]["id"] = component["id"]
        if "name" in component:
            html["attributes"]["class"] = component["name"].lower().replace(" ", "-")

        # Handle text content
        if component.get("type") == "TEXT":
            html["content"] = component.get("style", {}).get("text", {}).get("content", "")

        # Recursively process children
        for child in component.get("children", []):
            html["children"].append(self._generate_html(child))

        return html

    def _determine_html_tag(self, component: Dict[str, Any]) -> str:
        """Map Figma component types to HTML tags"""
        component_type = component.get("type", "").lower()
        if component_type == "text":
            return "span" if component.get("parent", {}).get("type") == "instance" else "p"
        elif component_type == "rectangle":
            return "div"
        elif component_type == "frame":
            return "section" if len(component.get("children", [])) > 1 else "div"
        elif component_type == "button":
            return "button"
        elif component_type == "input":
            return "input"
        elif component_type == "image":
            return "img"
        return "div"

    async def process_file(self):
        """Main processing pipeline"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # 1. Fetch complete file data
            print("Fetching Figma file data...")
            file_data = await self.fetch_entire_file(client)
            
            # 2. Extract all components
            print("Extracting components...")
            components = []
            if "document" in file_data:
                components = self._find_all_components(file_data["document"])
            
            # 3. Process each component
            print(f"Processing {len(components)} components...")
            for i, component_node in enumerate(components, 1):
                try:
                    print(f"Processing component {i}/{len(components)}: {component_node.get('name')}")
                    
                    # Extract comprehensive data
                    component_data = self.extract_component_data(component_node)
                    
                    # Fetch component image
                    image_data = await self.fetch_component_image(client, component_node["id"])
                    
                    # Save to file
                    await self._save_component(component_data, image_data)
                
                except Exception as e:
                    print(f"Error processing component: {str(e)}")
                    continue

            print("Processing complete!")

    def _find_all_components(self, node: Dict[str, Any], parent: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Recursively find all components in the document"""
        components = []
        
        # Check if current node is a component
        if node.get("type") == "COMPONENT" or node.get("type") == "COMPONENT_SET":
            components.append(node)
        
        # Process children recursively
        if "children" in node:
            for child in node["children"]:
                components.extend(self._find_all_components(child, node))
        
        return components

    async def _save_component(self, component_data: Dict[str, Any], image_data: Optional[bytes] = None):
        """Save component data to disk"""
        component_id = component_data["id"]
        safe_name = "".join(c if c.isalnum() else "_" for c in component_data["name"])
        filename = f"{safe_name}_{component_id}"
        
        # Save JSON data
        json_path = os.path.join(self.output_dir, f"{filename}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(component_data, f, indent=2, ensure_ascii=False)
        
        # Save image if available
        if image_data:
            img_path = os.path.join(self.output_dir, f"{filename}.svg")
            with open(img_path, "wb") as f:
                f.write(image_data)

async def main():
    # Configuration
    FIGMA_TOKEN = os.getenv("FIGMA_TOKEN")
    FILE_KEY = os.getenv("FIGMA_FILE_KEY")
    
    # Run processor
    processor = FigmaComponentExtractor(FIGMA_TOKEN, FILE_KEY)
    await processor.process_file()

if __name__ == "__main__":
    asyncio.run(main())
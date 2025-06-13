import os
import json
import asyncio
from typing import Dict, List, Optional, Any, Tuple, Union
import httpx
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import hashlib

load_dotenv()

class FigmaComponentExtractor:
    def __init__(self, personal_access_token: str, file_key: str):
        self.base_url = "https://api.figma.com/v1"
        self.headers = {"X-FIGMA-TOKEN": personal_access_token}
        self.file_key = file_key
        self.output_dir = "figma_components"
        self.timeout = httpx.Timeout(30.0, connect=60.0)
        Path(self.output_dir).mkdir(exist_ok=True)
        
        # Configuration for data extraction
        self.config = {
            "max_depth": 5,  # Maximum nesting level to process
            "include_images": True,
            "image_format": "svg",
            "image_scale": 1.0,
            "extract_styles": True,
            "extract_components": True,
            "extract_interactions": True,
            "extract_documentation": True
        }

    async def fetch_complete_file_data(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Fetch complete Figma file data including nodes, styles, and components."""
        params = {
            "depth": self.config["max_depth"],
            "geometry": "paths",
            "plugin_data": "shared" if self.config["extract_documentation"] else None
        }
        response = await client.get(
            f"{self.base_url}/files/{self.file_key}",
            headers=self.headers,
            params=params
        )
        response.raise_for_status()
        return response.json()

    async def fetch_component_metadata(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Fetch all components and component sets from the file"""
        response = await client.get(
            f"{self.base_url}/files/{self.file_key}/components",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    async def fetch_style_metadata(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        """Fetch all styles from the file"""
        if not self.config["extract_styles"]:
            return {}
            
        response = await client.get(
            f"{self.base_url}/files/{self.file_key}/styles",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    async def fetch_component_image(self, client: httpx.AsyncClient, node_id: str) -> Optional[str]:
        """Fetch SVG image for a component and return the SVG code"""
        params = {
            "ids": node_id,
            "format": self.config["image_format"],
            "scale": self.config["image_scale"]
        }
        response = await client.get(
            f"{self.base_url}/images/{self.file_key}",
            headers=self.headers,
            params=params
        )
        response.raise_for_status()
        image_refs = response.json().get("images", {})
        
        if node_id in image_refs and image_refs[node_id]:
            # Download the actual SVG content
            svg_response = await client.get(image_refs[node_id])
            svg_response.raise_for_status()
            return svg_response.text
        return None

    def extract_comprehensive_component_data(self, node: Dict[str, Any], parent: Dict[str, Any] = None, level: int = 0) -> Dict[str, Any]:
        """
        Extract comprehensive component data with full nested structure.
        Combines the detailed extraction from the first script with component-wise processing.
        """
        if level > self.config["max_depth"]:
            return {}
            
        node_type = node.get("type", "").lower()
        
        # Base component data structure
        component = {
            "metadata": {
                "id": node.get("id"),
                "name": node.get("name"),
                "type": node_type,
                "visible": node.get("visible", True),
                "locked": node.get("locked", False),
                "file_key": self.file_key,
                "timestamp": datetime.utcnow().isoformat(),
                "figma_url": f"https://www.figma.com/file/{self.file_key}/?node-id={node.get('id')}",
                "level": level
            },
            "parent": self._extract_parent_info(parent),
            "layout": self._extract_layout_data(node),
            "style": self._extract_style_data(node),
            "node_metadata": self._extract_node_metadata(node),
            "children": [],
            "svg_code": None,  # Will be populated later
            "description": None  # Will be generated later
        }
        
        # Add type-specific data
        if node_type == "component_set":
            component["component_set_data"] = self._extract_component_set_data(node)
        elif node_type == "instance":
            component["instance_data"] = self._extract_instance_data(node)
        elif node_type == "text":
            component["text_data"] = self._extract_text_data(node)
        elif node_type == "frame" or node_type == "group":
            component["frame_data"] = self._extract_frame_data(node)
        elif node_type == "rectangle":
            component["shape_data"] = self._extract_shape_data(node)
        elif node_type == "ellipse":
            component["shape_data"] = self._extract_shape_data(node)
        elif node_type == "vector":
            component["vector_data"] = self._extract_vector_data(node)
        
        # Process children recursively if within depth limit
        if "children" in node and level < self.config["max_depth"]:
            for child in node.get("children", []):
                child_data = self.extract_comprehensive_component_data(child, node, level + 1)
                if child_data:
                    component["children"].append(child_data)
        
        # Generate description
        component["description"] = self._generate_component_description(component)
        
        return component

    def _extract_parent_info(self, parent: Dict[str, Any]) -> Dict[str, Any]:
        """Extract parent node information"""
        if not parent:
            return {}
        return {
            "id": parent.get("id"),
            "name": parent.get("name"),
            "type": parent.get("type", "").lower()
        }

    def _extract_layout_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract all layout-related properties"""
        layout = {}
        
        # Basic dimensions and position
        if "absoluteBoundingBox" in node:
            bbox = node["absoluteBoundingBox"]
            layout.update({
                "x": bbox.get("x"),
                "y": bbox.get("y"),
                "width": bbox.get("width"),
                "height": bbox.get("height")
            })
        
        # Relative transform
        if "relativeTransform" in node:
            layout["transform"] = node["relativeTransform"]
        
        # Constraints
        if "constraints" in node:
            layout["constraints"] = {
                "horizontal": node["constraints"].get("horizontal"),
                "vertical": node["constraints"].get("vertical")
            }
        
        # Auto-layout properties
        if "layoutMode" in node:
            layout["auto_layout"] = {
                "direction": node.get("layoutMode"),
                "padding": {
                    "top": node.get("paddingTop"),
                    "right": node.get("paddingRight"),
                    "bottom": node.get("paddingBottom"),
                    "left": node.get("paddingLeft")
                },
                "item_spacing": node.get("itemSpacing"),
                "alignment": {
                    "primary": node.get("primaryAxisAlignItems"),
                    "counter": node.get("counterAxisAlignItems")
                },
                "sizing": {
                    "horizontal": node.get("layoutSizingHorizontal"),
                    "vertical": node.get("layoutSizingVertical")
                }
            }
        
        # Corner radius
        if "cornerRadius" in node:
            if isinstance(node["cornerRadius"], (int, float)):
                layout["corner_radius"] = node["cornerRadius"]
            elif isinstance(node["cornerRadius"], dict):
                layout["corner_radius"] = {
                    "top_left": node["cornerRadius"].get("topLeft"),
                    "top_right": node["cornerRadius"].get("topRight"),
                    "bottom_right": node["cornerRadius"].get("bottomRight"),
                    "bottom_left": node["cornerRadius"].get("bottomLeft")
                }
        
        # Grid layout
        if "layoutGrids" in node:
            layout["grids"] = [
                {
                    "pattern": grid.get("pattern"),
                    "section_size": grid.get("sectionSize"),
                    "gutter_size": grid.get("gutterSize"),
                    "alignment": grid.get("alignment"),
                    "count": grid.get("count"),
                    "offset": grid.get("offset")
                }
                for grid in node["layoutGrids"]
            ]
        
        return layout

    def _extract_style_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract all style-related properties"""
        style = {}
        
        # Fills (background)
        if "fills" in node and isinstance(node["fills"], list):
            style["fills"] = []
            for fill in node["fills"]:
                if fill.get("visible", True):
                    fill_data = {
                        "type": fill.get("type"),
                        "blend_mode": fill.get("blendMode"),
                        "opacity": fill.get("opacity", 1.0)
                    }
                    if fill.get("color"):
                        fill_data["color"] = self._normalize_color(fill["color"])
                    if fill.get("gradientStops"):
                        fill_data["gradient"] = [
                            {
                                "position": stop.get("position"),
                                "color": self._normalize_color(stop.get("color"))
                            }
                            for stop in fill["gradientStops"]
                        ]
                    if fill.get("type") == "IMAGE":
                        fill_data["image_ref"] = fill.get("imageRef")
                        fill_data["scale_mode"] = fill.get("scaleMode")
                    style["fills"].append(fill_data)
        
        # Strokes (borders)
        if "strokes" in node and isinstance(node["strokes"], list):
            style["strokes"] = []
            for stroke in node["strokes"]:
                if stroke.get("visible", True):
                    stroke_data = {
                        "type": stroke.get("type"),
                        "blend_mode": stroke.get("blendMode"),
                        "weight": node.get("strokeWeight", 1),
                        "position": node.get("strokeAlign", "INSIDE"),
                        "miter_limit": node.get("strokeMiterLimit"),
                        "join": node.get("strokeJoin"),
                        "cap": node.get("strokeCap")
                    }
                    if stroke.get("color"):
                        stroke_data["color"] = self._normalize_color(stroke["color"])
                    if stroke.get("gradientStops"):
                        stroke_data["gradient"] = [
                            {
                                "position": stop.get("position"),
                                "color": self._normalize_color(stop.get("color"))
                            }
                            for stop in stroke["gradientStops"]
                        ]
                    style["strokes"].append(stroke_data)
        
        # Effects (shadows, blurs)
        if "effects" in node and isinstance(node["effects"], list):
            style["effects"] = []
            for effect in node["effects"]:
                if effect.get("visible", True):
                    effect_data = {
                        "type": effect.get("type"),
                        "blend_mode": effect.get("blendMode"),
                        "radius": effect.get("radius", 0),
                        "spread": effect.get("spread", 0)
                    }
                    if effect.get("color"):
                        effect_data["color"] = self._normalize_color(effect["color"])
                    if effect.get("offset"):
                        effect_data["offset"] = effect["offset"]
                    style["effects"].append(effect_data)
        
        # Text styles
        if node.get("type") == "TEXT":
            style["text"] = {
                "content": node.get("characters", ""),
                "style": {
                    "font_family": node.get("style", {}).get("fontFamily"),
                    "font_weight": node.get("style", {}).get("fontWeight"),
                    "font_size": node.get("style", {}).get("fontSize"),
                    "line_height": node.get("style", {}).get("lineHeightPx"),
                    "letter_spacing": node.get("style", {}).get("letterSpacing"),
                    "text_case": node.get("style", {}).get("textCase"),
                    "text_decoration": node.get("style", {}).get("textDecoration"),
                    "paragraph_indent": node.get("style", {}).get("paragraphIndent"),
                    "paragraph_spacing": node.get("style", {}).get("paragraphSpacing")
                },
                "alignment": {
                    "horizontal": node.get("textAlignHorizontal"),
                    "vertical": node.get("textAlignVertical")
                },
                "auto_resize": node.get("textAutoResize")
            }
        
        # Blend mode and opacity
        if "blendMode" in node:
            style["blend_mode"] = node["blendMode"]
        if "opacity" in node:
            style["opacity"] = node["opacity"]
        
        return style

    def _normalize_color(self, color_data: Dict[str, float]) -> Dict[str, Any]:
        """Normalize color data to consistent format"""
        if not color_data:
            return None
            
        return {
            "r": color_data.get("r", 0),
            "g": color_data.get("g", 0),
            "b": color_data.get("b", 0),
            "a": color_data.get("a", 1.0),
            "hex": self._rgb_to_hex(color_data),
            "rgba": self._rgb_to_rgba(color_data)
        }

    def _rgb_to_hex(self, color: Dict[str, float]) -> str:
        """Convert RGB color to hex string"""
        r = int(round(color.get("r", 0) * 255))
        g = int(round(color.get("g", 0) * 255))
        b = int(round(color.get("b", 0) * 255))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _rgb_to_rgba(self, color: Dict[str, float]) -> str:
        """Convert RGB color to rgba string"""
        r = int(round(color.get("r", 0) * 255))
        g = int(round(color.get("g", 0) * 255))
        b = int(round(color.get("b", 0) * 255))
        a = round(color.get("a", 1.0), 2)
        return f"rgba({r}, {g}, {b}, {a})"

    def _extract_node_metadata(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract metadata and documentation for a node"""
        metadata = {
            "locked": node.get("locked", False),
            "export_settings": node.get("exportSettings"),
            "blend_mode": node.get("blendMode"),
            "preserve_ratio": node.get("preserveRatio"),
            "layout_version": node.get("layoutVersion"),
            "is_mask": node.get("isMask", False),
            "mask_type": node.get("maskType")
        }
        
        # Add plugin data if available
        if "pluginData" in node and self.config["extract_documentation"]:
            metadata["plugin_data"] = node["pluginData"]
        
        # Add shared plugin data
        if "sharedPluginData" in node and self.config["extract_documentation"]:
            metadata["shared_plugin_data"] = node["sharedPluginData"]
            
        # Add component properties if available
        if "componentProperties" in node:
            metadata["component_properties"] = node["componentProperties"]
            
        return metadata

    def _extract_component_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract component-specific data"""
        return {}  # Return empty dictionary instead of component data

    def _extract_component_set_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract component set-specific data"""
        return {
            "description": node.get("description"),
            "documentation_links": node.get("documentationLinks"),
            "key": node.get("key"),
            "property_definitions": node.get("componentPropertyDefinitions", {})
        }

    def _extract_instance_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract instance-specific data"""
        return {
            "component_id": node.get("componentId"),
            "overrides": node.get("overrides", []),
            "property_values": node.get("componentProperties", {}),
            "main_component": node.get("mainComponent", {}).get("id"),
            "scale_factor": node.get("scaleFactor")
        }

    def _extract_text_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract text-specific data"""
        return {
            "content": node.get("characters", ""),
            "style_id": node.get("styleId"),
            "hyperlink": node.get("hyperlink"),
            "auto_resize": node.get("textAutoResize"),
            "text_behavior": node.get("textBehavior"),
            "style_override_table": node.get("styleOverrideTable", {}),
            "character_style_overrides": node.get("characterStyleOverrides", []),
            "fills": node.get("fills", [])
        }

    def _extract_frame_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract frame/group-specific data"""
        return {
            "background_color": self._normalize_color(node.get("backgroundColor")),
            "clips_content": node.get("clipsContent"),
            "grid_style_ids": node.get("gridStyleIds", []),
            "guides": node.get("guides", []),
            "selection_background_color": self._normalize_color(node.get("selectionBackgroundColor"))
        }

    def _extract_shape_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract shape-specific data"""
        return {
            "corner_radius": node.get("cornerRadius"),
            "corner_smoothing": node.get("cornerSmoothing"),
            "rectangle_corner_radii": node.get("rectangleCornerRadii")
        }

    def _extract_vector_data(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Extract vector-specific data"""
        return {
            "stroke_weight": node.get("strokeWeight"),
            "stroke_align": node.get("strokeAlign"),
            "stroke_join": node.get("strokeJoin"),
            "stroke_cap": node.get("strokeCap"),
            "stroke_miter_limit": node.get("strokeMiterLimit"),
            "stroke_dashes": node.get("strokeDashes", []),
            "fill_geometry": node.get("fillGeometry", []),
            "stroke_geometry": node.get("strokeGeometry", [])
        }

    def _generate_component_description(self, component: Dict[str, Any]) -> str:
        """Generate a comprehensive description of the component"""
        metadata = component.get("metadata", {})
        layout = component.get("layout", {})
        style = component.get("style", {})
        children = component.get("children", [])
        
        description = f"This is a '{metadata.get('name', 'unnamed')}' component of type '{metadata.get('type', 'unknown')}'"
        
        # Add dimensions
        if "width" in layout and "height" in layout:
            description += f" with dimensions {round(layout['width'])}x{round(layout['height'])} pixels"
        
        # Add position
        if "x" in layout and "y" in layout:
            description += f", positioned at ({round(layout['x'])}, {round(layout['y'])})"
        
        # Add auto-layout info
        if "auto_layout" in layout:
            auto_layout = layout["auto_layout"]
            description += f", using '{auto_layout.get('direction', 'NONE')}' auto-layout"
            if auto_layout.get("alignment", {}).get("primary"):
                description += f" with primary alignment '{auto_layout['alignment']['primary']}'"
        
        # Add constraints
        if "constraints" in layout:
            constraints = layout["constraints"]
            description += f", constrained horizontally '{constraints.get('horizontal')}' and vertically '{constraints.get('vertical')}'"
        
        # Add style info
        if "fills" in style and style["fills"]:
            fill_count = len(style["fills"])
            description += f", with {fill_count} fill(s)"
        
        if "strokes" in style and style["strokes"]:
            stroke_count = len(style["strokes"])
            description += f", {stroke_count} stroke(s)"
        
        if "effects" in style and style["effects"]:
            effect_count = len(style["effects"])
            description += f", {effect_count} effect(s)"
        
        # Add children info
        if children:
            description += f", containing {len(children)} child element(s)"
        
        return description + "."

    def _find_all_components(self, node: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Recursively find all components and component sets in the document"""
        components = []
        
        node_type = node.get("type", "").upper()
        if node_type in ["COMPONENT", "COMPONENT_SET"]:
            components.append(node)
        
        # Process children recursively
        if "children" in node:
            for child in node["children"]:
                components.extend(self._find_all_components(child))
        
        return components

    async def save_component_data(self, component_data: Dict[str, Any], svg_code: Optional[str] = None):
        """Save component data to JSON file and SVG to separate file"""
        component_id = component_data["metadata"]["id"].replace(':', '-')
        safe_name = "".join(c if c.isalnum() or c in ['_', '-'] else "_" for c in component_data["metadata"]["name"])
        filename = f"{safe_name}_{component_id}"
        
        # Add SVG code to component data
        if svg_code:
            component_data["svg_code"] = svg_code
        
        # Save JSON data
        json_path = Path(self.output_dir) / f"{filename}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(component_data, f, indent=2, ensure_ascii=False)
        
        # Save SVG file if available
        if svg_code:
            svg_path = Path(self.output_dir) / f"{filename}.svg"
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_code)
        
        print(f"Saved component: {safe_name} -> {json_path}")

    async def process_file(self):
        """Main processing pipeline"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            print("Fetching Figma file data...")
            file_data = await self.fetch_complete_file_data(client)
            
            print("Fetching component metadata...")
            component_metadata = await self.fetch_component_metadata(client)
            
            print("Fetching style metadata...")
            style_metadata = await self.fetch_style_metadata(client)
            
            print("Finding all components...")
            components = []
            if "document" in file_data:
                components = self._find_all_components(file_data["document"])
            
            print(f"Found {len(components)} components to process")
            
            # Process each component
            for i, component_node in enumerate(components, 1):
                try:
                    component_name = component_node.get("name", f"Component_{i}")
                    print(f"Processing component {i}/{len(components)}: {component_name}")
                    
                    # Extract comprehensive component data
                    component_data = self.extract_comprehensive_component_data(component_node)
                    
                    # Fetch SVG image
                    svg_code = await self.fetch_component_image(client, component_node["id"])
                    
                    # Save component data and SVG
                    await self.save_component_data(component_data, svg_code)
                    
                except Exception as e:
                    print(f"Error processing component {component_name}: {str(e)}")
                    continue
            
            print(f"Processing complete! Saved {len(components)} components to {self.output_dir}")


async def main():
    FIGMA_TOKEN = os.getenv("FIGMA_API_KEY")
    FILE_KEY = os.getenv("FIGMA_FILE_ID")
    
    if not FIGMA_TOKEN or not FILE_KEY:
        print("Error: FIGMA_TOKEN and FIGMA_FILE_KEY must be set in environment variables")
        return
    
    extractor = FigmaComponentExtractor(FIGMA_TOKEN, FILE_KEY)
    await extractor.process_file()

if __name__ == "__main__":
    asyncio.run(main())
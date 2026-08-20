import fitz

def generate_sample_pdf(filename="test_paper.pdf"):
    doc = fitz.open()
    page = doc.new_page()
    
    # Add exam paper text
    page.insert_text((50, 50), "AQA GCSE Biology Higher Tier\nJune 2023 Paper 1", fontsize=16)
    page.insert_text((50, 100), "1(a) Name the organelle responsible for aerobic respiration in animal cells. [1 mark]", fontsize=12)
    page.insert_text((50, 200), "1(b) State two differences between plant and animal cells. [2 marks]", fontsize=12)
    page.insert_text((50, 300), "2(a) Explain how active transport moves mineral ions into plant root hair cells against a concentration gradient. [4 marks]", fontsize=12)
    
    # Add a vector diagram on the page (a cell schematic)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(50, 420, 250, 550))
    shape.draw_circle(fitz.Point(150, 485), 35)
    shape.finish(color=(0, 0, 0), fill=(0.85, 0.9, 1.0), width=2)
    shape.commit()
    page.insert_text((70, 570), "Figure 1: Schematic of root cell membrane", fontsize=10)
    
    doc.save(filename)
    print(f"Created {filename}")

if __name__ == "__main__":
    generate_sample_pdf()

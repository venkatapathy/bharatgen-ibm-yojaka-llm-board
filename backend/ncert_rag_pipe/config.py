# config.py
MASTER_CHAPTER_MAP = {
    "English": {
        "Math": {
            "10": ["Real Numbers", "Polynomials", "Pair of Linear Equations in Two Variables", "Quadratic Equations", "Arithmetic Progressions", "Triangles", "Coordinate Geometry", "Introduction to Trigonometry", "Some Applications of Trigonometry", "Circles", "Areas Related to Circles", "Surface Areas and Volumes", "Statistics", "Probability"],
            "11": ["Sets", "Relations and Functions", "Trigonometric Functions", "Complex Numbers and Quadratic Equations", "Linear Inequalities", "Permutations and Combinations", "Binomial Theorem", "Sequences and Series", "Straight Lines", "Conic Sections", "Introduction to Three Dimensional Geometry", "Limits and Derivatives"],
            "12": ["Inverse Trigonometric Functions", "Matrices", "Determinants", "Continuity and Differentiability", "Application of Derivatives", "Integrals", "Application of Integrals", "Differential Equations", "Vector Algebra", "Three Dimensional Geometry", "Linear Programming"]
        },
        "Physics": {
            "10": ["Light – Reflection and Refraction", "The Human Eye and the Colourful World", "Electricity", "Magnetic Effects of Electric Current", "Sources of Energy"],
            "11": ["Units and Measurements", "Motion in a Straight Line", "Motion in a Plane", "Laws of Motion", "Work, Energy and Power", "System of Particles and Rotational Motion", "Gravitation", "Mechanical Properties of Solids", "Mechanical Properties of Fluids", "Thermal Properties of Matter", "Thermodynamics", "Kinetic Theory", "Oscillations", "Waves"],
            "12": ["Electric Charges and Fields", "Electrostatic Potential and Capacitance", "Current Electricity", "Moving Charges and Magnetism", "Magnetism and Matter", "Electromagnetic Induction", "Alternating Current", "Electromagnetic Waves", "Ray Optics and Optical Instruments", "Wave Optics", "Dual Nature of Radiation and Matter", "Atoms", "Nuclei", "Semiconductor Electronics: Materials, Devices and Simple Circuits"]
        },
        "Chemistry": {
            "10": ["Chemical Reactions and Equations", "Acids, Bases and Salts", "Metals and Non-metals", "Carbon and its Compounds", "Periodic Classification of Elements"],
            "11": ["Some Basic Concepts of Chemistry", "Structure of Atom", "Classification of Elements and Periodicity in Properties", "Chemical Bonding and Molecular Structure", "Thermodynamics", "Equilibrium", "Redox Reactions", "Organic Chemistry: Some Basic Principles and Techniques", "Hydrocarbons"],
            "12": ["Solutions", "Electrochemistry", "Chemical Kinetics", "The d- and f-Block Elements", "Coordination Compounds", "Haloalkanes and Haloarenes", "Alcohols, Phenols and Ethers", "Aldehydes, Ketones and Carboxylic Acids", "Amines", "Biomolecules"]
        },
        "Biology": {
            "10": ["Life Processes", "Control and Coordination", "How do Organisms Reproduce?", "Heredity and Evolution", "Our Environment", "Management of Natural Resources"],
            "11": ["The Living World", "Biological Classification", "Plant Kingdom", "Animal Kingdom", "Morphology of Flowering Plants", "Anatomy of Flowering Plants", "Structural Organisation in Animals", "Cell: The Unit of Life", "Biomolecules", "Cell Cycle and Cell Division", "Photosynthesis in Higher Plants", "Respiration in Plants", "Plant Growth and Development", "Breathing and Exchange of Gases", "Body Fluids and Circulation", "Excretory Products and their Elimination", "Locomotion and Movement", "Neural Control and Coordination", "Chemical Coordination and Integration"],
            "12": ["Sexual Reproduction in Flowering Plants", "Human Reproduction", "Reproductive Health", "Principles of Inheritance and Variation", "Molecular Basis of Inheritance", "Evolution", "Human Health and Disease", "Microbes in Human Welfare", "Biotechnology: Principles and Processes", "Biotechnology and its Applications", "Organisms and Populations", "Ecosystem", "Biodiversity and Conservation"]
        }
    },
    "Hindi": {
        "Math": {
            "10": ["वास्तविक संख्याएँ", "बहुपद", "दो चर वाले रैखिक समीकरण युग्म", "द्विघात समीकरण", "समांतर श्रेणियाँ", "त्रिभुज", "निर्देशांक ज्यामिति", "त्रिकोणमिति का परिचय", "त्रिकोणमिति के कुछ अनुप्रयोग", "वृत्त", "वृत्तों से संबंधित क्षेत्रफल", "पृष्ठीय क्षेत्रफल और आयतन", "सांख्यिकी", "प्रायिकता"],
            "11": ["समुच्चय", "संबंध एवं फलन", "त्रिकोणमितीय फलन", "सम्मिश्र संख्याएँ और द्विघातीय समीकरण", "रैखिक असमिकाएँ", "क्रमचय और संचय", "द्विपद प्रमेय", "अनुक्रम तथा श्रेणी", "सरल रेखाएँ", "शंकु परिच्छेद", "त्रिविमीय ज्यामिति का परिचय", "सीमा और अवकलज"],
            "12": ["प्रतिलोम त्रिकोणमितीय फलन", "आव्यूह", "सारणिक", "सांतत्य तथा अवकलनीयता", "अवकलज के अनुप्रयोग", "समाकलन", "समाकलनों के अनुप्रयोग", "अवकल समीकरण", "सदिश बीजगणित", "त्रिविमीय ज्यामिति", "रैखिक प्रोग्रामन"]
        },
        "Physics": {
            "10": ["प्रकाश – परावर्तन तथा अपवर्तन", "मानव नेत्र तथा रंगबिरंगा संसार", "विद्युत", "विद्युत धारा के चुंबकीय प्रभाव", "ऊर्जा के स्रोत"],
            "11": ["मात्रक और मापन", "सरल रेखा में गति", "समतल में गति", "गति के नियम", "कार्य, ऊर्जा और शक्ति", "कणों के निकाय तथा घूर्णी गति", "गुरुत्वाकर्षण", "ठोसों के यांत्रिक गुण", "तरलों के यांत्रिक गुण", "द्रव्य के तापीय गुण", "ऊष्मागतिकी", "अणुगति सिद्धांत", "दोलन", "तरंगें"],
            "12": ["वैधुत आवेश तथा क्षेत्र", "स्थिरवैधुत विभव तथा धारिता", "विद्युत धारा", "गतिमान आवेश और चुंबकत्व", "चुंबकत्व एवं द्रव्य", "वैधुतचुंबकीय प्रेरण", "प्रत्यावर्ती धारा", "वैधुतचुंबकीय तरंगें", "किरण प्रकाशिकी एवं प्रकाशिक यंत्र", "तरंग-प्रकाशिकी", "विकिरण तथा द्रव्य की द्वैत प्रकृति", "परमाणु", "नाभिक", "अर्धचालक इलेक्ट्रॉनिकी - पदार्थ, युक्तियाँ तथा सरल परिपथ"]
        },
        "Chemistry": {
            "10": ["रासायनिक अभिक्रियाएँ एवं समीकरण", "अम्ल, क्षारक एवं लवण", "धातु एवं अधातु", "कार्बन एवं उसके यौगिक", "तत्वों का आवर्त वर्गीकरण"],
            "11": ["रसायन विज्ञान की कुछ मूल अवधारणाएँ", "परमाणु की संरचना", "तत्वों का वर्गीकरण एवं गुणधर्मों में आवर्तिता", "रासायनिक आबंधन तथा आण्विक संरचना", "ऊष्मागतिकी", "साम्यावस्था", "अपचयोपचय अभिक्रियाएँ", "कार्बनिक रसायन: कुछ आधारभूत सिद्धांत तथा तकनीकें", "हाइड्रोकार्बन"],
            "12": ["विलयन", "वैधुतरसायन", "रासायनिक बलगतिकी", "d- एवं f- ब्लॉक के तत्व", "उपसहसंयोजन यौगिक", "हैलोएल्केन तथा हैलोएरीन", "ऐल्कोहॉल, फ़ीनॉल एवं ईथर", "ऐल्डिहाइड, कीटोन एवं कार्बोक्सिलिक अम्ल", "ऐमीन", "जैव-अणु"]
        },
        "Biology": {
            "10": ["जैव प्रक्रम", "नियंत्रण एवं समन्वय", "जीव जनन कैसे करते हैं?", "आनुवंशिकता एवं जैव विकास", "हमारा पर्यावरण", "प्राकृतिक संसाधनों का प्रबंधन"],
            "11": ["जीव जगत", "जीव जगत का वर्गीकरण", "वनस्पति जगत", "प्राणि जगत", "पुष्पी पादपों की आकारिकी", "पुष्पी पादपों का शरीर", "प्राणियों में संरचनात्मक संगठन", "कोशिका: जीवन की इकाई", "जैव अणु", "कोशिका चक्र और कोशिका विभाजन", "उच्च पादपों में प्रकाश-संश्लेषण", "पादप में श्वसन", "पादप वृद्धि एवं परिवर्धन", "श्वसन और गैसों का विनिमय", "शरीर द्रव तथा परिसंचरण", "उत्सर्जी उत्पाद एवं उनका निष्कासन", "गमन एवं संचलन", "तंत्रिकीय नियंत्रण एवं समन्वय", "रासायनिक समन्वय तथा एकीकरण"],
            "12": ["पुष्पी पादपों में लैंगिक जनन", "मानव जनन", "जनन स्वास्थ्य", "वंशागति तथा विविधता के सिद्धांत", "वंशागति के आण्विक आधार", "विकास", "मानव स्वास्थ्य तथा रोग", "मानव कल्याण में सूक्ष्मजीव", "जैव प्रौद्योगिकी - सिद्धांत व प्रक्रम", "जैव प्रौद्योगिकी एवं उसके उपयोग", "जीव और समष्टियाँ", "पारितंत्र", "जैव-विविधता एवं संरक्षण"]
        }
    }
}
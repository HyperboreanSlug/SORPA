#!/usr/bin/env python3
"""Expand ethnic_names.json comprehensively (add only; never drop existing)."""
from __future__ import annotations

import json
from pathlib import Path


def merge_list(*parts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for name in part:
            n = (name or "").strip()
            if not n:
                continue
            key = n.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(n)
    return sorted(out, key=lambda x: x.lower())


def merge_dict(base: dict, extra: dict) -> dict:
    out = {}
    keys = sorted(set(base) | set(extra), key=str.lower)
    for k in keys:
        out[k] = merge_list(base.get(k, []), extra.get(k, []))
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "scraper" / "ethnic_names.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    # --- Hispanic / Latino (US common + regional) ---
    hispanic_add = """
    Garcia Rodriguez Martinez Hernandez Lopez Gonzalez Perez Sanchez Ramirez Torres
    Flores Rivera Gomez Diaz Cruz Morales Ortiz Ramos Gutierrez Alvarez Castillo
    Jimenez Ruiz Reyes Mendoza Herrera Medina Vargas Sandoval Campos Romero Delgado
    Cabrera Castaneda Rojas Soto Aguilar Navarro Mendez Serrano Vazquez Molina Ortega
    Guerrero Escobar Figueroa Alvarado Nunez Carrillo Rios Salazar Santiago Castro
    Deleon Arias Valdez Maldonado Vega Ochoa Cisneros Valencia Pineda Acosta Guzman
    Fuentes Munoz Marquez Contreras Dominguez Estrada Gallegos Garza Ibarra Juarez
    Leon Luna Macias Mejia Miranda Montoya Pacheco Padilla Pena Quiroz Robles
    Rosales Rubio Salinas Solis Suarez Trevino Trujillo Velasquez Villegas Zavala
    Zamora Zuniga Ayala Benitez Blanco Calderon Camacho Cardenas Cervantes Chavez
    Coronado Cortez Cuevas Davila Duarte Escobedo Espinoza Franco Galindo Gallardo
    Gamez Guillen Huerta Lara Leal Lozano Magana Marroquin Melendez Mercado Montes
    Najera Nava Olivares Orozco Palacios Parra Peralta Quintero Rangel Rocha Rodriquez
    Rosas Saucedo Serrato Solorzano Sosa Tapia Tellez Trejo Uribe Valle Vasquez
    Velasco Venegas Vera Vidal Villanueva Ybarra Zambrano Zepeda Alcantara Arellano
    Avalos Avila Barajas Barrientos Becerra Beltran Bernal Briones Cano Cantu Cardona
    Carmona Carrasco Carrera Casas Cedillo Chacon Collazo Cordova Corona Correa
    Covarrubias Delacruz Delarosa Delatorre Esparza Esquivel Felix Galvez Godinez
    Granados Guajardo Guardado Guevara Hidalgo Jaimes Ledesca Limon Lugo Mares
    Marin Mariscal Mata Melgoza Mireles Montemayor Montiel Mora Mota Murillo Najera
    Ocampo Ontiveros Oseguera Osorio Paredes Partida Pedraza Pelayo Pimentel Ponce
    Portillo Prado Puente Pulido Quezada Quintana Renteria Resendiz Reyna Rico
    Rivas Robledo Rocha Rodarte Rojo Romo Rueda Saavedra Salcedo Saldana Sanabria
    Santana Santillan Segura Sierra Sifuentes Solorio Sotelo Tamez Tovar Urbina
    Valadez Valenzuela Valles Varela Vela Velazquez Villa Villalobos Yanez Zarate
    Acevedo Aguiar Alcaraz Almanza Almonte Amaya Anaya Andujar Aparicio Aragon
    Arce Armenta Arredondo Arriaga Arteaga Astorga Avalos Ayala Banda Barba Barcenas
    Barrios Batista Bautista Becerra Bedolla Bello Benavides Bernal Betancourt
    Bohorquez Bonilla Borrego Bravo Brito Burgos Bustamante Caballero Caceres
    Calvillo Camarena Campuzano Canales Cardenas Carrion Casillas Castanon
    Castellanos Castrejon Cazares Cedeno Celis Cepeda Cerda Chairez Chavarria
    Cisneros Collazo Colon Concepcion Cordero Cordova Coronel Corral Correa
    Cortes Cosme Costilla Cotto Covarrubias Cuellar Davila Deanda Deanda
    Delatorre Delgadillo Delrio Delvalle Deras Domenech Donato Duran Elizondo
    Escalante Escamilla Espinal Espinosa Estevez Fabian Fajardo Farias Ferrer
    Figueroa Fonseca Fragoso Frias Galacia Galindo Galvan Gamez Garcia Garibay
    Garrido Gaspar German Giron Godinez Gracia Granados Guajardo Guardado
    Guillen Gutierrez Guzman Heredia Hermosillo Hernandez Herrera Hidalgo
    Holguin Huerta Ibanez Iglesias Infante Jaimes Jaramillo Jauregui Jimenez
    Juarez Jurado Laboy Landa Lara Ledesma Leiva Lerma Leyva Limon Lizarraga
    Loera Longoria Lopez Lozada Lozano Lugo Luna Macias Madera Magana Maldonado
    Manzano Marcial Marin Marroquin Martell Martin Martin del Campo Martinez
    Mascareñas Mata Mateos Mayorga Medellin Medina Mejia Melendez Mena Mendez
    Mendoza Meneses Mercado Merino Meza Mireles Mojica Molina Monroy Montalvo
    Montanez Montano Montejano Montemayor Montenegro Montes Montoya Mora Morales
    Moreno Mota Moya Muniz Munoz Murillo Naranjo Narvaez Nava Navarro Negrete
    Negron Nieto Nolasco Noriega Nunez Ocampo Ochoa Orozco Ortega Ortiz Osorio
    Otero Ovalle Pacheco Padilla Padron Paez Palacios Pantoja Paredes Parra
    Partida Pascual Pastrana Pedraza Pena Peralta Perdomo Perez Pichardo Pineda
    Pizarro Plata Plaza Ponce Portillo Posada Prado Prieto Puente Pulido
    Quezada Quihuis Quinones Quintana Quiroz Ragan Ramirez Ramon Ramos Rangel
    Raya Real Rebolledo Recinos Redondo Rendon Renteria Resendiz Restrepo Reyes
    Reyna Rico Rincon Rios Rivas Rivera Rivero Robles Rocha Rodarte Rodrigues
    Rodriguez Rojas Rojo Roman Romero Romo Rosado Rosales Rosas Roybal Rubio
    Rueda Ruiz Saavedra Saenz Salas Salazar Salcedo Salcido Saldana Saldivar
    Salgado Salinas Samaniego Sanabria Sanchez Sandoval Sanmiguel Santana
    Santiago Santillan Santos Sauceda Saucedo Segovia Segura Serrano Sevilla
    Sierra Sifuentes Silva Solano Solis Solorzano Soria Sosa Soto Sotelo
    Suarez Tamez Tapia Tellez Terrazas Tinoco Tirado Toledo Torres Tovar
    Trejo Trevino Trujillo Umana Urbina Urias Uribe Urrutia Valadez Valdes
    Valdez Valencia Valenzuela Valle Valles Valverde Varela Vargas Vasquez
    Vazquez Vega Vela Velasco Velasquez Velazquez Velez Venegas Vera Vergara
    Vidal Viera Villa Villalobos Villanueva Villarreal Villegas Yanez Ybarra
    Zambrano Zamora Zapata Zarate Zavala Zepeda Zuniga
    """

    # --- Asian: East / Southeast only ---
    asian_extra = {
        "chinese": """
            Chen Wang Li Zhang Liu Yang Huang Wu Zhao Zhou Xu Sun Ma Zhu Hu Guo He Lin Gao Luo
            Zheng Xie Tang Han Cao Deng Deng Feng Feng Bai Bao Bi Bian Bu Cai Cen Che Chu
            Cui Dai Dan Deng Ding Dong Dou Du Duan Fan Fang Fei Feng Fu Gan Geng Gong Gu Guan
            Gui Guo Hai Hang Hao He Hou Hu Hua Huan Huang Hui Ji Jia Jian Jiang Jiao Jin Jing
            Ju Kang Ke Kong Kuang Lai Lan Lang Lao Lei Leng Li Lian Liang Liao Lin Ling Liu
            Long Lou Lu Luan Luo Lv Ma Mai Mao Mei Meng Mi Miao Min Ming Mo Mou Mu Na Nan
            Ni Nie Ning Niu Ou Pan Pang Pei Peng Pi Ping Pu Qi Qian Qiang Qiao Qin Qing Qiu
            Qu Quan Rao Ren Rong Ruan Rui Sa Sang Sha Shang Shao Shen Sheng Shi Shou Shu
            Shui Si Song Su Sui Sun Suo Tai Tan Tang Tao Teng Tian Tong Tu Wan Wang Wei Wen
            Weng Wo Wu Xi Xia Xiang Xiao Xie Xin Xing Xiong Xu Xue Xun Yan Yang Yao Ye Yi
            Yin Ying Yong You Yu Yuan Yue Yun Zang Zeng Zha Zhan Zhang Zhao Zhen Zheng Zhi
            Zhong Zhou Zhu Zhuang Zhuo Zong Zou Wong Tan Lim Ng Chan Cheng Chung Fung Ho Lau
            Leung Mak Tsang Yeung Yip Cheung Chow Chu Fong Kwok Lai Lam Law Lui Pang Siu
            Tong Tse Yeo Yuen So Chiu Hui Kwan Kwong Mok Ngai Poon Shum Tam Tsui Yim Yu
            Goh Teo Koh Ong Chua Wee Quek Ang Toh Hooi Soh Yap Chew Liew
        """,
        "korean": """
            Kim Lee Park Choi Jung Kang Cho Yoon Han Oh Seo Shin Kwon Hwang Ahn Song Hong
            Ko Moon Bae Baek Cha Chae Ha Im Jang Jeon Nam Ryu Yoo An Bang Byun Do Eom
            Go Gwak Ham Heo Hyun Jeong Ji Jin Jo Jun Ki Kil Koo Kyeom Min Myung Noh Paik
            Pyo Ra Seok Seong Sim Sohn Son Suh Suk Sung Tak Woo Yeo Yeom Yi Yim Yoon You
            Yun Choe Chun Chung Han Heo Hong Hyeon In Jeong Jin Joo Ju Jun Kang Kwon
            Lim Min Na Noh Oh Park Rhee Ryu Seo Shim Shin So Sohn Song Won Yang
        """,
        "vietnamese": """
            Nguyen Tran Le Pham Hoang Huynh Ngo Vu Do Bui Dang Vo Duong Mai Thai Phan Truong
            Ho Ly Quach Trinh Lam Cao Dinh Ha Dao Diep Doan Giang Ha Hang Hien Hieu Hoa
            Hung Huu Khanh Kieu Loan Loc Luong Minh My Nam Nghi Nghiem Nhan Nhung Phong
            Phuc Phuong Quan Quang Quoc Sang Son Tai Tam Thao Thinh Tho Thu Thuy Tien Toan
            Tong Tra Trang Tri Trong Tuan Tung Tuyet Uyen Van Vinh Xuan Yen Bach Cam Chau
            Cuc Dieu Hanh Hien Hieu Hong Hue Huy Khanh Khoa Kiet Lan Linh Loan Long Luu
            Manh Nghiem Nhat Phat Phuong Quyen Suong Thach Thanh That Thuan Tinh Tram
            Trieu Trinh Truong Tu Tung Uyen Van Vuong
        """,
        "japanese": """
            Yamamoto Tanaka Ito Nakamura Kobayashi Sato Watanabe Yoshida Yamada Suzuki
            Takahashi Kato Shimizu Yamazaki Mori Ikeda Inoue Kimura Hayashi Saito Matsumoto
            Yamaguchi Abe Fujita Aoki Endo Fujii Fukuda Goto Hasegawa Hashimoto Hirano
            Hoshino Ishida Ishii Ishikawa Iwasaki Kaneko Kikuchi Kishimoto Kojima Kondo
            Maeda Matsuda Matsui Miyamoto Miyazaki Mizuno Murakami Nakajima Nakano
            Nishimura Noguchi Ogawa Okada Okamoto Okumura Ono Ota Otsuka Sakamoto
            Sakurai Sasaki Sugiyama Takada Takagi Takano Takeuchi Tanabe Uchida Ueda
            Ueno Wada Yamashita Yokoyama Yoshida Yoshikawa Yoshino Ando Arai Araki
            Asano Chiba Doi Eguchi Fukui Fukumoto Fukushima Furukawa Hamada Harada
            Hattori Hirata Hirose Honda Hori Iida Imai Inoue Iwata Kanai Kaneko Kato
            Kawaguchi Kawakami Kawamoto Kawasaki Kishi Kitamura Kono Kubo Kudo
            Kurihara Kuroda Maruyama Masuda Matsuo Miki Miyake Miyata Mizutani
            Morita Murata Nagai Nagata Nakagawa Nakata Nishida Nishiyama Nomura
            Ogawa Okazaki Okubo Omori Onishi Oshima Sano Sawada Seki Shibata
            Shimada Sugimoto Sugita Takashima Takeuchi Tamura Tani Taniguchi
            Terauchi Toda Tokunaga Tomita Tsuchiya Tsuji Tsutsui Uemura Wakabayashi
            Yano Yasuda Yokota Yoshida Yoshioka
        """,
        "thai": """
            Srisawat Chaiyaphum Suksamran Thongchai Rattanakul Phanit Wongsawat Chaiyasit
            Saengchan Boonsri Charoenkul Chaiyaporn Jantarasorn Kittisak Limthongchai
            Phakdi Phromphitak Prasert Rattana Saetang Sawatdee Siriwan Somsri
            Sooksai Sriphong Sukkasem Supaporn Thavorn Wongchai Yodrak Boonyarat
            Chaiwat Intarasuwan Kaewmanee Namwong Phetchaburi Phichet Pimchan
            Rungruang Samart Sangsuk Srisuk Thanakit Thongdee Wattana
        """,
        "filipino": """
            Bautista Dela Cruz Villanueva Aquino Magbanua Macapagal Panganiban Manalo
            Salvador Mercado Alcantara Pascual Galang Dimaculangan Enriquez Abadilla
            Abella Abesamis Acal Agbayani Agudo Alano Alcaraz Alfaro Alonzo Alviar
            Amador Andrada Angeles Anonuevo Antonio Apostol Aragon Araneta Arevalo
            Asuncion Atienza Austria Avila Bacani Baguio Bala Baluyut Banaag Banayat
            Banez Banzon Barrientos Basilio Batungbacal Bautista Bayani Belmonte
            Benitez Bernabe Bernardo Blanco Bondoc Borja Briones Buenaventura
            Caballero Cabral Cabrera Cadiz Calma Camacho Canlas Capistrano Carlos
            Carmona Carpio Castillo Castro Catapang Cayabyab Cayetano Celis Cervantes
            Chavez Claudio Concepcion Cordero Corpuz Cortez Crisostomo Cruz Cuaresma
            Cuevas Custodio Dacanay Dagdagan Dalisay Dantes David De Guzman De Jesus
            De Leon De Mesa De Vera Del Mundo Del Rosario Delos Reyes Delos Santos
            Diaz Diego Dimaculangan Dizon Domingo Dominguez Dueñas Dumlao
            Elefante Elizalde Encarnacion Escobar Esguerra Espino Esguerra Esteban
            Estrada Evangelista Fabian Fajardo Felipe Fernandez Ferrer Flores
            Fontanilla Francisco Franco Galang Galvez Gamboa Garcia Gaspar
            Gatchalian Gomez Gonzaga Gonzales Gonzalez Guevarra Gutierrez Guzman
            Hernandez Herrera Ignacio Ilagan Ibarra Javier Jimenez Jose
            Jurado Labrador Lacson Lagua Lagua Lagman Lagrimas Laguardia Laguerta
            Laguio Lamarca Landicho Langit Lantion Lao Lapuz Lara Laranang
            Laserna Lastimosa Latorre Laurente Lavares Laya Layug Lazaro
            Leal Ledesma Lee Legaspi Lemuel Leoncio Leonor Lerma Leyva
            Lim Limoso Linao Lising Lising Llamas Llanera Llorente
            Lo Lobo Locsin Lontoc Lopez Lorenzo Loyola Lozada Lozano
            Lucero Luciano Lugo Luis Lumbres Luna Lungay Macalintal
            Macapagal Macaraeg Macaspac Maceda Madrigal Magbanua Magno Magpantay
            Magtibay Makabenta Malabanan Malonzo Manahan Manalo Manansala
            Mandigma Maniego Manimtim Manlapaz Manlutac Manongdo Manzano
            Mapua Maranan Marcelo Marcos Mariano Marquez Martinez Mateo
            Medina Mejia Melendez Mendoza Mercado Mercado Miranda Mojica
            Molina Monje Montano Montemayor Montenegro Montes Montilla
            Morales Moreno Mota Munoz Murillo Nacario Nadal Naga
            Natividad Navarro Neri Nieto Nolasco Noriega Nuñez Ocampo
            Ochoa Ocampo Ojeda Olivares Ong Ortega Ortiz Osorio
            Pacheco Padilla Padua Pagaduan Paguio Pajaron Palacio
            Panganiban Pangilinan Paraiso Paredes Pascual Pastor
            Patawaran Paterno Pena Peralta Perez Pimentel Pineda
            Pizarro Plaza Ponce Portillo Prado Prieto Puente
            Quezada Quijano Quinto Quiroz Ramos Rangel Real
            Reyes Rico Rincon Rios Rivas Rivera Robles Rocha
            Rodriguez Rojas Roman Romero Romo Rosales Rosas
            Ruiz Saavedra Salazar Salcedo Saldana Salgado
            Salinas Sanchez Sandoval Santiago Santos Saucedo
            Segura Serrano Sevilla Sierra Silva Solis Soria
            Sosa Soto Suarez Tamez Tapia Torres Tovar
            Trejo Trujillo Urbina Uribe Valadez Valdez
            Valencia Valenzuela Valle Vargas Vasquez Vega
            Velasquez Vera Vergara Vidal Villa Villalobos
            Villanueva Villarreal Villegas Yanez Zamora
            Zarate Zavala
        """,
        "indonesian": """
            Santoso Wijaya Setiawan Hidayat Susanto Saputra Pratama Nugroho
            Wibowo Kurniawan Hakim Suryadi Firmansyah Gunawan Hermawan
            Irawan Prasetyo Rahayu Sari Putri Utami Wati Lestari
            Handayani Maulana Fauzi Ramadhan Syahputra Abdullah Yusuf
            Nasution Siregar Lubis Harahap Sitompul Manurung Simanjuntak
            Sihombing Nainggolan Purba Ginting Tarigan Sitepu Perangin
            Widodo Suharto Sukarno Habibie Yudhoyono Widodo Jokowi
            Aditya Budi Cahyo Darmawan Eko Fajar Galih Hadi
            Indra Jaya Kartika Lestari Made Nyoman Putu Wayan
            Agus Bambang Dedi Endang Fitri Guntur Hendra
            Irfan Joko Kiki Lina Maya Nia Oki Putra
            Rina Santi Tono Udin Vina Wulan Yani Zaki
        """,
        "malaysian": """
            Abdullah Ahmad Ali Hassan Ibrahim Ismail Mahmud Omar
            Rahman Razak Salleh Yusof Zainal Aziz Bakar
            Cheong Chong Goh Ho Koh Lim Ong Tan
            Teo Wong Yap Yeo Chan Chin Chua
            Kamal Khalid Latif Musa Nasir Osman
            Rashid Salleh Shamsuddin Yusof Zulkifli
            Azman Faizal Hafiz Hamzah Idris Jamil
            Kamaludin Mazlan Mohd Norazlan Razali
            Rosli Sulaiman Wahab Zakaria
        """,
        "cambodian": """
            Sok Chea Chan Dara Heng Keo Kim Meas
            Phirun Piseth Rithy Samnang Sopheak Soriya
            Sophal Srey Thy Vannak Vicheka Vuthy
            Chhay Chheang Chhorn Chhoun Chim
            Dy Ea Eang Em Eng Ho
            Huot Huoy Keat Keo Khat Khem
            Khin Khlot Khou Khun Khy Kong
            Kuon Kuy Leng Lim Lon Long
            Ly Ma Mao Meas Men Mey
            Moeun Mok Mom Muong Muy Nang
            Neang Nem Neou Ngor Nguon Nhim
            Nhoek Nhorn Nou Nup Ouk
            Oum Pech Peou Phan Phat
            Pheach Pheap Pheap Pich Pinn
            Po Pol Pov Prak Prum
            Puth Roeun Ros Ros Sam
            Samat Sambath Samoeun Samreth
            San Sanh Sar Sarin Saroeun
            Sarom Sarun Sath Savoeun
            Seng Seang Seam Sean
            Seang Seila Sem Seng
            Sin Sinat Sith Sithon
            So Soeun Sokhom Sokun
            Som Soman Somet Somnang
            Son Sopheap Sorphorn Sorya
            Soth Sourn Soy Srey
            Sros Srun Suos Suy
            Taing Tep Thach Thay
            Then Thol Thom Thong
            Thy Tieng Tim Tin
            Toch Touch Touch Touch
            Tuch Tum Tun Ty
            Uch Uk Un Ung
            Uon Uy Van Vann
            Vath Veng Vin Voeun
            Von Vong Vuthy
        """,
        "hmong": """
            Vang Yang Xiong Lee Lor Moua Thao Vue
            Her Hang Cha Cheng Kong Kue
            Ly Pha Chang Chue Fang
            Khang Lo Phang Saechao
            Saelee Saephan Saeteurn
            Saechao Thoj Vang Xiong
            Yang Vue Moua Lor Lee
        """,
        "laotian": """
            Phommachanh Phommasone Sisavath Souvannavong Vongphachanh
            Bounnhong Chanthavong Inthavong Keomany Khamphoumy
            Phaengsi Phomvihane Sayavong Sisouk Soukthavong
            Thammavong Vannavong Vilayphone Xayavong
            Boupha Chanthalangsy Insisienmay Keophila
            Khamvongsa Phommachack Phothisane
            Sisomphone Souvannarath Vongdara
        """,
        "burmese": """
            Aung Kyaw Min Myint Hlaing Oo Than
            Win Zaw Htun Htet Naing Myo
            Soe Thant Htay Khin Maung
            Nyein Phyo Sein Thein
            Aye Bo Bo Cho Daung
            Eain Hnin Htoo Khine
            Lwin Moe Moe Nanda
            Nu Nu Pwint San San
            Shwe Su Su Thida
            Thuzar Tin Tin Wai Wai
            Ye Ye Yin Yin
        """,
        "mongolian": """
            Batbayar Batjargal Boldbaatar Enkhbaatar Erdenebat
            Ganbaatar Munkhbat Nergui Otgonbayar Purev
            Sukhbaatar Tserenbaatar Batmunkh
            Chimedtseren Dorj Enkhtuya
            Gantulga Khulan Munkhzul
            Narantuya Oyunchimeg Saruul
            Solongo Tuya Uuganbayar
        """,
    }

    # --- Indian / South Asian: split by region ---
    indian_existing = data.get("indian_surnames", [])
    if isinstance(indian_existing, dict):
        indian_flat = []
        for v in indian_existing.values():
            if isinstance(v, list):
                indian_flat.extend(v)
    else:
        indian_flat = list(indian_existing or [])

    indian_groups = {
        "india": merge_list(
            indian_flat,
            """
            Patel Shah Singh Kumar Gupta Agarwal Joshi Pandey Verma Mehta Rao Reddy
            Nair Iyer Pillai Srivastava Sharma Chopra Kapoor Malhotra Bhatia Khanna
            Arora Bansal Saxena Tiwari Mishra Chaturvedi Desai Trivedi Pathak Dwivedi
            Banerjee Mukherjee Chatterjee Bose Das Ghosh Sen Dutta Iyengar Menon
            Nambiar Krishnan Raman Subramanian Venkatesh Naidu Jain Parekh Modi Amin
            Bhatt Dave Gandhi Thakkar Kulkarni Jadhav Patil Deshmukh More Gaikwad
            Shinde Pawar Bhattacharya Chakraborty Roy Saha Biswas Mondal Basu
            Aggarwal Ahluwalia Ahuja Anand Apte Atwal Bajaj Bakshi Bala Balakrishnan
            Balasubramanian Banerji Barua Basak Batra Bedi Bhalla Bhardwaj Bhargava
            Bhasin Bhat Bhattacharjee Bhattacharyya Bhave Bhave Bhonsle Bhushan
            Bisht Bora Borah Borpujari Bose Burman Chadha Chakrabarti Chakraborty
            Chandra Chaturvedi Chauhan Chawla Cheema Chhabra Choudhary Chowdhury
            Dabholkar Dalal Dang Dasgupta Datta Dayal De Deol Deshmukh Deshpande
            Dewan Dey Dhawan Dixit Dogra Doshi Dubey Duggal Dutta Engineer
            Gadgil Ganguly Gargi Ghatak Ghoshal Godbole Goel Gokhale Gopal
            Goswami Goyal Grewal Guha Gulati Haldar Hegde Inamdar Iqbal
            Irani Iyengar Iyer Jadeja Jagtap Jain Jaiswal Jha Jindal
            Johar Joshi Juneja Kabir Kakkar Kalra Kamath Kamat Kanetkar
            Kannan Kapadia Kapur Kar Karandikar Karnik Kashyap Kataria
            Kaul Kaushik Kelkar Khosla Khurana Kohli Kolhapure Kothari
            Krishnamurthy Krishnaswamy Kulkarni Kumar Kundu Lakhani Lal
            Luthra Madan Mahajan Maheshwari Majumdar Malik Mallya Mani
            Manjrekar Mathur Mehra Mehta Menon Merchant Mirchandani Mishra
            Mitra Mittal Modi Mohan Mohanty Mookerjee Mudaliar Mukherjee
            Mukhopadhyay Munjal Murthy Nadar Nadkarni Nag Nagarajan Nair
            Nambiar Nanda Narain Narayan Narayanan Nath Nayar Nehru Nigam
            Oberoi Ojha Om Padmanabhan Pal Paliwal Pandit Pant Parekh
            Parikh Patel Pathak Patil Patnaik Paul Pillai Pinto Poddar
            Prabhu Prakash Prasad Pratap Punjabi Puri Purohit Qureshi
            Raghavan Rai Raina Raj Rajan Raju Ramachandran Raman
            Ramaswamy Rane Rangaswamy Rao Rastogi Rathore Rau Ravindran
            Ray Reddy Rege Roy Roychowdhury Sabharwal Saha Sahni Saini
            Salvi Sampath Sanyal Sarin Sarkar Sarma Sathe Saxena
            Sen Sengupta Seth Sethi Shah Shanbhag Shankar Sharma
            Shetty Shinde Shirodkar Shukla Sidhu Singh Sinha Sircar
            Somani Sood Sridhar Srinivasan Srivastava Subramaniam
            Sundaram Suresh Swamy Tagore Talwar Tandon Tarkas Thakur
            Thapar Thatte Tiwari Trivedi Tyagi Uddin Upadhyay Vaidya
            Varma Varadarajan Varghese Venkataraman Venkatesan Verma
            Vij Vijay Virk Viswanathan Vohra Wadhwa Wagle Walia
            Yadav Yagnik Zachariah Zaidi
            Agarwal Aggarwal Ahlawat Anand Apte Arora Awasthi
            Bachchan Bajpai Balan Balasubramaniam Banerjee Barua
            Basu Bedi Bhagat Bhandari Bhatnagar Bhattacharya
            Bhonsle Biswas Bose Burman Chadha Chakrabarti
            Chandra Chawla Cheema Chopra Choudhury
            Dalal Dasgupta Datta Deol Desai Deshmukh
            Dewan Dixit Doshi Dubey Dutt Dutta
            Engineer Gadgil Ganguly Ghatak Ghosh
            Godbole Goel Gokhale Goswami Goyal
            Guha Gulati Haldar Hegde Inamdar
            Jadeja Jagtap Jaiswal Jha Jindal
            Johar Juneja Kakkar Kalra Kamath
            Kannan Kapadia Kapur Karandikar
            Kashyap Kataria Kaul Kaushik
            Kelkar Khosla Khurana Kohli
            Kothari Krishnamurthy Kulkarni
            Kundu Lakhani Lal Luthra
            Madan Mahajan Maheshwari Majumdar
            Mallya Manjrekar Mathur Mehra
            Merchant Mirchandani Mitra Mittal
            Mohanty Mookerjee Mudaliar Munjal
            Murthy Nadar Nadkarni Nagarajan
            Nanda Narayan Narayanan Nath
            Nayar Nehru Nigam Oberoi Ojha
            Padmanabhan Paliwal Pant Parikh
            Patnaik Paul Pinto Poddar
            Prabhu Prakash Prasad Pratap
            Punjabi Puri Purohit Raghavan
            Raj Rajan Raju Ramachandran
            Ramaswamy Rane Rangaswamy Rastogi
            Rathore Rau Ravindran Ray
            Rege Roychowdhury Sabharwal Sahni
            Saini Salvi Sampath Sanyal
            Sarin Sarma Sathe Sengupta
            Seth Sethi Shanbhag Shankar
            Shetty Shirodkar Shukla Sidhu
            Sinha Sircar Somani Sood
            Sridhar Srinivasan Subramaniam
            Sundaram Suresh Swamy Tagore
            Talwar Tandon Tarkas Thakur
            Thapar Thatte Tyagi Upadhyay
            Vaidya Varadarajan Varghese
            Venkataraman Venkatesan Vij
            Vijay Virk Viswanathan Vohra
            Wadhwa Wagle Walia Yadav
            Yagnik Zachariah Zaidi
            """.split(),
        ),
        "pakistani": merge_list(
            """
            Khan Ahmed Sheikh Qureshi Malik Siddiqui Ansari Mirza Hussain
            Rahman Ali Hassan Mahmood Iqbal Raza Shahzad Butt
            Chaudhry Cheema Bajwa Gill Sandhu Sidhu Brar
            Awan Bhatti Dar Ghauri Hashmi Javed
            Kazmi Lodhi Mahmood Naqvi Niazi
            Paracha Rehman Rizvi Syed Tariq
            Usmani Wahab Zahid Zafar
            Abbasi Afridi Akhtar Alam
            Anwar Aslam Azam Aziz
            Baig Bashir Bhatti Bukhari
            Chughtai Durrani Farooq
            Ghani Gilani Haider
            Hamid Hasan Hossain
            Imran Inayat Ishaq
            Jahan Jahangir Jalal
            Jamal Jamil Javaid
            Kamal Karim Khalid
            Khokhar Latif Mahmood
            Majeed Mansoor Masood
            Mir Mughal Munir
            Mushtaq Nadeem Nasir
            Nawaz Noman Noor
            Parvez Qadir Qamar
            Qazi Rafiq Rashid
            Rauf Riaz Sabir
            Saeed Saghir Salim
            Sami Sattar Shafi
            Shahbaz Shareef Sharif
            Shoaib Sohail Sultan
            Tahir Talat Tanvir
            Umar Usman Waqar
            Waseem Yaqub Yasin
            Younis Yousaf Zafar
            Zahoor Zaidi Zia
            """.split()
        ),
        "bangladeshi": merge_list(
            """
            Rahman Islam Hasan Uddin Hossain Chowdhury Miah Sarker
            Ahmed Ali Khan Hussain Karim Alam
            Begum Biswas Das Ghosh
            Haque Hossain Howlader
            Islam Kabir Khan
            Mahmud Mia Molla
            Mondal Parvin Rahman
            Rashid Saha Sarkar
            Sheikh Sikder Talukder
            Akter Alam Amin
            Azad Bhuiyan Biswas
            Choudhury Das Dewan
            Faruk Gazi Haider
            Hakim Halder Hoque
            Hossain Howlader
            Jahan Joarder Kabir
            Karim Khan Mahbub
            Mahmud Majumder Mallick
            Mannan Mia Miah
            Molla Mondal Munshi
            Nasrin Parvin Rahman
            Rashid Rony Saha
            Sarkar Shikdar Sikder
            Talukder Uddin
            """.split()
        ),
        "sri_lankan": merge_list(
            """
            Fernando Perera Silva Jayawardena Wickramasinghe Bandara
            Wijesinghe Dissanayake Gunawardena Rathnayake
            De Silva Fonseca Cooray Dias
            Ekanayake Gooneratne Gunasekara
            Herath Jayasuriya Karunaratne
            Liyanage Mendis Pathirana
            Rajapaksa Ranasinghe Samarakoon
            Senanayake Seneviratne Silva
            Wijeratne Amarasinghe Balasuriya
            Chandrasekera Corea De Alwis
            De Mel De Zoysa Dharmadasa
            Galappaththy Gamage Gunawardene
            Hettiarachchi Jayatilleke Jayawardene
            Karunatilaka Kodikara Kumarasinghe
            Liyanarachchi Mudalige Nanayakkara
            Peiris Pieris Premadasa
            Rajapakse Ratnayake Samaraweera
            Seneviratna Sirisena Tissera
            Weerasinghe Wijekoon
            """.split()
        ),
        "nepali": merge_list(
            """
            Thapa Gurung Rai Limbu Shrestha Maharjan Adhikari
            Basnet Bhandari Bhattarai Chhetri Dahal
            Gautam Ghimire Karki Khadka
            Koirala Lama Magar Pandey
            Poudel Pradhan Rana Regmi
            Sapkota Sharma Subedi Tamang
            Acharya Aryal Bista Budhathoki
            Chand Dangol Devkota Dhakal
            Joshi KC Khanal Khatri
            Kunwar Luitel Mahat Neupane
            Oli Pandit Parajuli Pathak
            Pokharel Rijal Shah Shahi
            Thakuri Timilsina
            """.split()
        ),
    }

    # --- African American (distinctive + common diaspora) ---
    aa_add = """
    Washington Jefferson Booker Freeman Mosley Baptiste Pierre Desir Diallo Keita
    Traore Kamara Mensah Okeke Carver Douglass Tubman Bluford Smalls Gullah
    Boykins Witherspoon Pickett Hartsfield Winfield Banks Beasley Bolden
    Braxton Bridges Britt Brockman Byrd Calhoun Cannon Chambers
    Clayton Cleaver Clemmons Cleveland Cobb Colbert
    Coleman Coles Compton Conley Cotton
    Crenshaw Crump Dabney Dandridge
    Davenport Dawkins Delaney Diggs
    Dixon Dorsey Dotson Dunbar
    Early Ellison Epps Everett
    Fairfax Farmer Faulkner Fitch
    Flood Foreman Fortson Frazier
    Gaines Gant Garnett Gaskins
    Gholston Gipson Goode Grady
    Graves Greenidge Grimes Grier
    Grigsby Gunter Hagans Hairston
    Hargrove Harrell Hatcher Haynes
    Hearn Hemphill Henson Hightower
    Hilliard Holley Holloman Holman
    Holsey Hood Hopewell Horton
    Huggins Hull Hurt Ingram
    Ivory Jeffries Jemison Jeter
    Jett Joiner Jolley Joyner
    Justice Kendrick Keyes Kilgore
    Kinard Kinsey Kirkland Knox
    Lacy Lamar Lampkin Lanier
    Lathan Lawton Leake Leak
    Ledbetter Lightfoot Lipscomb Littlejohn
    Lockett Lovejoy Lowery Lyles
    Mabry Mack Macon Maddox
    Magwood Manigault Mansell Marable
    Mapp Massey Mathis Mayfield
    Mays McAfee McCall McClain
    McClellan McClendon McCray McCullough
    McFadden McGee McKinnon McKoy
    McNeil McRae Means Mickens
    Mingo Minnis Mixon Mobley
    Moffett Monds Monk Montague
    Moody Mooney Moon Moorehead
    Moseley Moss Motley Moultrie
    Mullins Munford Murrell Muse
    Myrick Napier Nash Naylor
    Nellum Nesbitt Newby Newell
    Newsom Newton Nickens Noble
    Norfleet Norwood Oaks Odom
    Ogletree Oliphant Oliver O'Neal
    Outlaw Overstreet Owens Pace
    Page Paige Palmer Pannell
    Parker Parks Parrish Parson
    Paschal Pate Patrick Patterson
    Patton Paulk Payne Peacock
    Peak Pearson Peebles Peete
    Pendergrass Penn Peoples Perkins
    Perrin Perry Person Pettaway
    Pettiford Pettigrew Pettus Petty
    Phelps Philpot Pickens Pierce
    Pinckney Pinkney Pinkston Pipkin
    Pittman Pitts Player Pleasant
    Plummer Poe Pointer Polk
    Pollard Pompey Pool Pope
    Porter Posey Poteat Potter
    Powe Powell Powers Prather
    Pratt Pressley Price Pride
    Priest Primus Prince Pringle
    Pritchard Pritchett Proctor Pruitt
    Pryor Puckett Pugh Pulliam
    Purcell Purdy Purvis Quarles
    Quattlebaum Quince Quinn Ragsdale
    Raines Rainey Ramey Ramsey
    Randolph Rankin Ransom Ratliff
    Rawls Rayford Rayner Reaves
    Redd Redding Redmond Reese
    Reeves Register Reid Renfroe
    Renshaw Rentz Revels Rhodes
    Rice Rich Richards Richardson
    Richey Ricks Riddick Ridley
    Riggs Riggins Riggsby Riles
    Riley Rimmer Rivers Roach
    Roberson Roberts Robertson Robeson
    Robins Robinson Roby Rochell
    Rockett Rodgers Rodney Rogers
    Roker Roland Roles Rollins
    Rooks Roper Rose Rosser
    Roundtree Rouse Rousey Rowan
    Rowe Rowell Rowland Roxbury
    Royal Royall Roye Rucker
    Ruffin Rufus Ruggs Rule
    Rush Rushing Russell Rutherford
    Rutledge Ryals Ryan Ryans
    Saddler Sadler Sain Saxton
    Scales Scarborough Scarbrough Scurry
    Seabrook Seal Seals Searcy
    Sears Seay Sedberry Seeley
    Sellers Sells Settle Seward
    Sewell Sexton Seymour Shackleford
    Shanklin Shannon Sharpe Sharper
    Shaver Shaw Shealey Sheard
    Sheffield Shelby Shell Shelton
    Shepard Shepherd Sheppard Sheridan
    Sherlock Sherman Sherrill Sherrod
    Shields Shivers Shockley Shores
    Short Shorter Shotwell Showers
    Shuler Shumate Sibley Sidbury
    Siddell Sides Sikes Silas
    Siler Silvers Simkins Simmons
    Simms Simon Simons Simpkins
    Simpson Sims Sinclair Singletary
    Singleton Sink Sinkfield Sinkler
    Sizemore Skeens Skelton Skinner
    Slaughter Sledge Slocum Smalls
    Smart Smiley Smith Smoot
    Snead Sneed Snell Snelling
    Snipes Snow Snowden Solomon
    Sorey Sorrow Southerland Sowell
    Spain Spangler Spann Sparks
    Sparrow Speaks Spearman Spears
    Speight Spellman Spence Spencer
    Spigner Spikes Spiller Spinks
    Spivey Spotswood Spratling Spratt
    Spriggs Springer Sprott Spruill
    Spurgeon Spurlock Stacey Stack
    Stackhouse Staley Stallings Stallworth
    Stamps Stanback Stancil Stanford
    Stanley Stansbury Stanton Staples
    Stapleton Starks Starling Starnes
    Starr Staten Staton Staub
    Staves Steed Steedman Steel
    Steele Steen Stegall Stenhouse
    Stephens Stephenson Sterling Stevens
    Stevenson Steward Stewart Stickney
    Stiles Stillwell Stinson Stith
    Stitt Stivers Stocks Stockton
    Stogner Stokes Stokley Stone
    Stoney Storey Storm Stoudemire
    Stout Stovall Stover Stowers
    Strachan Strait Strange Stratford
    Stratton Strauss Street Streets
    Stribling Strickland Stringer Stringfellow
    Strother Stroud Stroup Strudwick
    Stuart Stubbs Stuckey Studivant
    Sturdivant Sturgeon Sturgis Sturkie
    Styers Suddeth Sudduth Suggs
    Suit Sullins Sullivan Sullivant
    Summerall Summerford Summers Summerville
    Sumner Sumter Surratt Surrency
    Suttles Sutton Swain Swann
    Swanson Swaringen Swearengin Sweat
    Sweeney Sweet Swift Swindell
    Swinton Switzer Swope Sykes
    Sylvester Tabb Tabor Tackett
    Tait Taitt Talbert Talbot
    Talbott Taliaferro Talkington Talley
    Tallman Talton Tandy Tann
    Tanner Tapp Tarbutton Tarleton
    Tarpley Tarr Tarrance Tarver
    Tatum Tawes Taylor Teague
    Teal Teasley Tedder Teel
    Teeter Teeters Tefft Tellis
    Temple Templeton Tenney Tennyson
    Terrell Terrill Terry Teske
    Tester Thacker Tharp Thatcher
    Thaxton Thayer Thedford Therrell
    Thigpen Thoman Thomas Thomason
    Thomasson Thompkins Thompson Thomson
    Thorne Thornhill Thornton Thorp
    Thorpe Thrasher Threadgill Threatt
    Thrift Thrower Thurber Thurman
    Thurmond Thurston Tibbs Tice
    Tidwell Tierney Tigert Tilden
    Tilghman Till Tillery Tilley
    Tillman Tillotson Tilson Timberlake
    Timmons Timmerman Timmons Tinsley
    Tipton Tisdale Tisdell Titshaw
    Titus Tobias Tobin Todd
    Tolbert Toliver Tolleson Tolley
    Tollison Tolliver Tolson Tom
    Tomlin Tomlinson Tompkins Toney
    Toole Tooley Toombs Toomer
    Toon Toone Toothman Tope
    Torbett Torrence Torrence Torrey
    Toth Totten Touchstone Toulson
    Towers Towle Townes Townley
    Towns Townsend Townsell Townson
    Toy Trabue Tracey Tracy
    Trahan Trammell Trapp Travers
    Travis Traywick Treadaway Treadwell
    Treat Tredway Treece Tremble
    Trent Trepagnier Trest Trexler
    Tribble Trice Trickett Trigg
    Trimble Trimm Trimmier Triplett
    Trippe Trippet Trivett Trivette
    Trott Trotter Troup Trout
    Troutman Trowbridge Troxell Troy
    Truelove Truett Truitt Trull
    Truman Trumbo Trump Truss
    Trussell Trusty Tryon Tubbs
    Tucci Tucker Tudor Tuggle
    Tull Tullis Tully Tumblin
    Tumlin Tunnell Tunney Tunstall
    Turbeville Turbiville Turbyfill Turcotte
    Turley Turnage Turnbow Turner
    Turney Turnipseed Turpin Turrentine
    Tuttle Tutwiler Twiggs Twitty
    Twyman Tyndall Tyner Tyree
    Tyrrell Tyson Udall Uhl
    Ulrich Ulmer Underdown Underhill
    Underwood Unger Upchurch Updike
    Upshaw Upton Urban Urbanski
    Ussery Utley Utt Utter
    Utz Vail Vaillancourt Valadez
    Valentine Vales Valiant Valle
    Vallier Vallo Vallotton Valois
    Van VanAllen Vanarsdale VanBuren
    Vance Vancil Vanderburg Vanderford
    Vandergrift Vanderpool Vandiver Vandyke
    Vann Vanover Vanpelt Vansant
    Vantassell Vanvleet Vanwinkle Vanzant
    Varnell Varner Varnes Varney
    Varon Vasser Vaughan Vaughn
    Vaught Vawter Veach Veal
    Veasey Veatch Vedder Veeder
    Veenstra Vega Veitch Vela
    Velasco Velasquez Velez Venable
    Venables Vencill Venne Vennum
    Venters Ventress Ventura Venus
    Verble Verdin Verdon Vereen
    Verge Verghese Verhey Verhoeven
    Verlinden Vermeulen Vermillion Vern
    Verna Verne Verner Vernon
    Verry Verser Vertrees Vestal
    Vest Vestel Vester Vickers
    Vickery Vicknair Vicks Victor
    Victoria Vidal Vidrine Vieau
    Vierra Viers Vieux Vigeant
    Vigen Vignes Vigo Vigue
    Vila Vilas Vilches Villa
    Villafane Villalobos Villanueva Villar
    Villarreal Villasenor Villegas Villeneuve
    Villerreal Villiers Vines Vining
    Vinson Vinyard Viola Violette
    Vipperman Virgil Virgin Virgo
    Virtue Visconti Vise Viser
    Visor Vita Vitale Vitello
    Vito Vittitoe Vivanco Viviano
    Vivo Vliet Vo Voelker
    Voelz Vogan Voge Vogel
    Vogelsang Vogler Vohs Voight
    Voigt Voiles Voils Voirin
    Voisin Vokes Voland Volante
    Volberding Volbrecht Vold Volk
    Volkman Volkmann Volkmer Voll
    Vollmer Volmer Volpe Voltz
    Volz VonCannon Vondra Vondracek
    Vong Vonschriltz Vonsteen Voorhees
    Voorhies Voorhis Vorce Vore
    Vories Voris Voros Vorpahl
    Vorse Vorse Vose Voshell
    Voss Votaw Voth Vought
    Voulgaris Vovk Vowell Vowels
    Vowles Vox Voyen Voyles
    Voytek Vrabec Vrabel Vradenburg
    Vreeland Vroman Vrooman Vukelich
    Vukovic Vulgamore Vulgamott Vuncannon
    """.split()

    # --- Native American ---
    na_add = """
    Bear Bird Cedar Cloud Coyote Crow Dove Eagle Fox Hawk Hunter Iron Little
    Long Moccasin Morning Mountain Pine Red Rock Running Sage Sharp Silver
    Spirit Stone Tall Thunder Two Wolf Blackbear Bluebird Brave Bull
    Crazyhorse Deer Feather Fire Flying Hawkman
    Horse Laughing Lightning Many
    Night Owl Prairie Rain
    River Shadow Sky Smoke
    Snake Snow Standing Star
    Strong Sun Swan Talking
    Three Walking Water White
    Wind Yellow Youngblood
    Begay Benally Bitsui Chee
    Claw Tsosie Yazzie Nez
    Etcitty Platero Skeets
    Todacheene Todachine
    """.split()

    # --- European expansions ---
    european_extra = {
        "german": """
            Muller Schmidt Schneider Fischer Weber Meyer Wagner Becker Schulz Hoffmann
            Koch Richter Braun Krueger Zimmermann Bauer Hartmann Lange Werner
            Schmitz Krause Meier Lehmann Huber Kaiser Fuchs Vogel Friedrich
            Scholz Ritter Busch Berger Keller Wolf Neumann Schwarz Weiss
            Roth Gross Klein Stein Berger Brandt Engel Haas Jung
            Berger Berger Berger
            Mueller Schmidt Schneider Fischer Weber Meyer Wagner Becker
            Schulz Hoffmann Koch Richter Braun Krueger Zimmermann Bauer
            Hartmann Lange Werner Schmitz Krause Meier Lehmann Huber
            Kaiser Fuchs Vogel Friedrich Scholz Ritter Busch Berger
            Keller Wolf Neumann Schwarz Weiss Roth Gross Klein
            Stein Brandt Engel Haas Jung Berger
            Albrecht Arnold Bach Baumann Beck Berger
            Blum Bohr Brandt Braun Busch Dietrich
            Ebert Engel Fischer Franke Franz
            Fuchs Gebhardt Geiger Graf Gross
            Haas Hahn Hartmann Heinze Herrmann
            Huber Jung Kaiser Keller Klein
            Koch Kramer Krause Kruger Kuhn
            Lange Lehmann Lorenz Ludwig Maier
            Mayer Meier Meyer Muller Neumann
            Otto Peters Pfeiffer Pohl Richter
            Roth Sauer Schaefer Schmidt Schneider
            Schroeder Schubert Schulze Schwarz
            Seidel Simon Sommer Stein Thomas
            Vogel Voigt Wagner Walter Weber
            Weiss Werner Winkler Winter Wolff
            Zimmermann Ziegler
        """.split(),
        "italian": """
            Rossi Russo Ferrari Esposito Bianchi Romano Colombo Siciliano Conti
            Mancini Costa Giordani Rizzo Lombardi Moretti Barbieri Gallo
            Greco Bruno Ricci Marino Greco Greco Greco
            Bruno Bruno Bruno Bruno Bruno
            Caruso Costa Fontana Gentile Gallo
            Giordano Greco Leone Lombardo Mancini
            Marino Martinelli Martino Morelli Moretti
            Neri Orlando Palmieri Pellegrini Ricci
            Rinaldi Romano Rossi Russo Sala
            Santoro Serra Siciliano Silvestri Testa
            Valentini Vitale Amato Barbera Basile
            Battaglia Benedetti Bernardi Bianco
            Caputo Carbone Caruso Cattaneo
            Conte Coppola Costa De Angelis
            De Luca De Rosa Esposito Fabbri
            Farina Fiore Fontana Galli
            Gallo Gatti Gentile Giordano
            Giuliani Greco Guerra Longo
            Lombardi Lombardo Mancini Marchetti
            Mariani Marino Martinelli Martini
            Mazza Messina Montanari Morelli
            Moretti Neri Orlando Pagano
            Palmieri Parisi Pellegrini Rizzi
            Romano Rossi Ruggiero Russo
            Sanna Santoro Serra Silvestri
            Sorrentino Testa Valentini Villa
            Vitale
        """.split(),
        "french": """
            Martin Bernard Dubois Thomas Robert Richard Petit Durand Leroy Moreau
            Simon Laurent Lefebvre Michel David Bertrand Roux Vincent
            Fournier Morel Girard Andre Lefevre Mercier Dupont Lambert
            Bonnet Francois Martinez Rousseau Blanc Guerin Muller
            Henry Roussel Nicolas Perrin Mathieu Clement Gauthier
            Dumont Lopez Fontaine Chevalier Robin Masson Sanchez
            Gerard Nguyen Boyer Denis Lemaire Duval Joly
            Gautier Garcia Roche Roy Noel Meyer Lucas
            Meunier Jean Picard Marchand Dumas Lacroix
            Fabre Gaillard Breton Marchal Renaud
            Colin Morin Arnaud Vidal Brun
            Marie Reynaert Caron Renard
            Pires Blanchard Adam Picard
            Noel Lemaitre Charpentier
            Giraud Rolland Renard
        """.split(),
        "irish": """
            O'Brien O'Connor O'Sullivan Murphy Kelly Walsh Ryan Doyle McCarthy
            Kennedy Gallagher Quinn Duffy Reilly Brennan Byrne Carroll
            Connolly Daly Doherty Donnelly Doyle Dunne Farrell
            Fitzgerald Fitzpatrick Flanagan Flynn Foley
            Healy Hughes Johnston Kavanagh Keane
            Lynch Maguire Maher McDermott McDonagh
            McGrath McMahon McNamara Molloy Moore
            Moran Mulcahy Murray Nolan O'Neill
            O'Reilly Power Quinn Regan
            Roche Ryan Sheehan Smyth
            Sullivan Sweeney Tobin Walsh
            Whelan White
            O'Brien O'Connor O'Sullivan Murphy Kelly
            Walsh Ryan Doyle McCarthy Kennedy
            Gallagher Quinn Duffy Reilly Brennan
            Byrne Carroll Connolly Daly Doherty
            Donnelly Dunne Farrell Fitzgerald
            Fitzpatrick Flanagan Flynn Foley
            Healy Hughes Johnston Kavanagh
            Keane Lynch Maguire Maher
            McDermott McDonagh McGrath McMahon
            McNamara Molloy Moore Moran
            Mulcahy Murray Nolan O'Neill
            O'Reilly Power Quinn Regan
            Roche Sheehan Smyth Sullivan
            Sweeney Tobin Whelan White
        """.split(),
        "scottish": """
            MacDonald Campbell Stewart Murray Wilson Cameron Craig Fraser Gordon
            Irvine MacLean Mackay McLeod Ross Watson Anderson Brown
            Clark Duncan Ferguson Graham Grant
            Hamilton Henderson Hunter Johnston
            Kerr MacGregor MacIntosh MacKenzie
            MacPherson McDonald McIntyre McKay
            McLean Mitchell Morrison Reid
            Robertson Scott Sinclair Smith
            Taylor Thomson Walker Young
            Aitken Bain Black Boyd
            Bruce Buchan Burns Calder
            Christie Cunningham Davidson
            Douglas Drummond Findlay
            Fleming Forbes Forsyth
            Gillespie Gray Guthrie
            Hay Innes Keith Kennedy
            Lindsay MacArthur MacAulay
            MacDougall MacInnes MacLachlan
            MacMillan MacNeil MacRae
            Menzies Moffat Munro
            Napier Ramsay Ritchie
            Rutherford Shaw Strachan
            Sutherland Wallace Watt
        """.split(),
        "polish": """
            Nowak Kowalski Wisniewski Wojcik Krawczyk Zielinski Kozlak Szymanski
            Wozniak Dabrowski Kozlowski Jankowski Mazur Kwiatkowski Kaczmarek
            Piotrowski Grabowski Nowakowski Pawlowski Michalski Nowicki
            Adamczyk Dudek Zajac Wieczorek Jablonski Krol Majewski
            Olszewski Jaworski Wrobel Malinowski Pawlak Witkowski
            Walczak Stepien Gorski Rutkowski Michalak
            Sikora Ostrowski Baran Tomaszewski Pietrzak
            Marciniak Wroblewski Zalewski Jakubowski
            Jasiński Zawadzki Sadowski Borkowski
            Czarnecki Sawicki Chmielewski Sokolowski
        """.split(),
        "russian": """
            Ivanov Smirnov Kuznetsov Popov Vasiliev Petrov Sokolov
            Mikhailov Novikov Fedorov Morozov Volkov
            Alekseev Lebedev Semenov Egorov Pavlov
            Kozlov Stepanov Nikolaev Orlov
            Andreev Makarov Nikitin Zakharov
            Zaitsev Soloviev Borisov Yakovlev
            Grigoriev Romanov Vorobiev Sergeev
            Kuzmin Frolov Alexandrov Dmitriev
            Korolev Gusev Kiselev Ilyin
            Maximov Polyakov Gavrilov
            Bogdanov Osipov Titov
            Markov Belov Komarov
        """.split(),
        "greek": """
            Papadopoulos Georgiou Dimitriou Nikolaou Ioannou
            Constantinou Christodoulou Andreou
            Panagiotou Antoniou Petrou
            Markou Vasileiou Alexandrou
            Theodorou Michael Michaelides
            Kyriakou Charalambous Economou
            Christou Pappas Katsaros
            Kostas Dimitriadis
            Papadakis Georgiadis
            Nikolaidis Ioannidis
        """.split(),
        "dutch": """
            De Jong De Vries Jansen Bakker Visser Smit Meijer
            De Boer Mulder De Groot Bos
            Vos Peters Hendriks Van Dijk
            Dekker Van Leeuwen Brouwer
            De Wit Dijkstra Smits
            De Bruijn Van Den Berg
            Van Der Meer Vermeulen
            Van Der Berg Schouten
            Van Der Heijden
        """.split(),
        "scandinavian": """
            Johansson Andersson Karlsson Nilsson Eriksson Larsson
            Olsson Persson Svensson Gustafsson
            Pettersson Jonsson Jansson Hansson
            Bengtsson Jönsson Lindberg Lindström
            Lindgren Axelsson Bergström Lundberg
            Lundgren Jakobsson Berg Berglund
            Fredriksson Sandberg Mattsson
            Henriksson Forsberg Sjöberg
            Holm Hansen Olsen Larsen
            Johansen Nilsen Pedersen
            Kristiansen Jensen Nielsen
            Andersen Christensen Petersen
            Thomsen Poulsen Rasmussen
        """.split(),
        "english": """
            Smith Jones Williams Brown Taylor Davies Evans Wilson
            Thomas Roberts Johnson Lewis Walker
            Robinson Wood Thompson White
            Watson Jackson Wright Green
            Harris Edwards Collins Hughes
            Price Hall Morris Morgan
            Cooper King Scott Baker
            Harris Clarke Allen Young
            Adams Hill Wright Scott
        """.split(),
        "ukrainian": """
            Shevchenko Kovalenko Bondarenko Tkachenko Boyko
            Kravchenko Kovalchuk Melnyk Shevchuk
            Polishchuk Bondar Lysenko Marchenko
            Rudenko Savchenko Petrenko Moroz
            Oliynyk Tkachuk Koval Yurchenko
            Klymenko Pavlenko Sydorenko
            Hrytsenko Vasylenko Khomenko
        """.split(),
        "czech": """
            Novak Svoboda Novotny Dvorak Cerny Prochazka
            Kucera Vesely Horak Nemec
            Pokorny Marek Posel Ruzicka
            Benes Fiala Sedlacek Dolezal
            Zeman Kolář Navratil Cermak
            Urban Blaha Kraus
        """.split(),
        "hungarian": """
            Nagy Kovacs Toth Szabo Horvath Varga Kiss
            Molnar Nemeth Farkas Balogh
            Gulyas Papp Takacs Juhasz
            Lakatos Meszaros Olah Simon
            Racz Fekete Szilagyi Török
        """.split(),
    }

    jewish_add = """
    Cohen Kaplan Bernstein Goldman Schwartz Rosenberg Katz Goldstein Rosenbaum
    Friedman Weiss Levy Abramowitz Stern Shapiro Waxman Goldberg
    Greenberg Hoffman Levine Silverman Klein
    Rosenfeld Zimmerman Abrams Adler
    Berger Blum Feldman Freedman
    Grossman Halpern Heller Horowitz
    Jacobs Kaufman Kessler Landau
    Lieberman Margolis Meyers Nussbaum
    Perlman Rabinowitz Resnick Roth
    Rubin Sandler Schneider Siegel
    Steinberg Strauss Waldman Weiner
    Weinstein Weissman Wolfson Zucker
    Abramson Appelbaum Aronson Becker
    Berkowitz Berman Bernstein Blatt
    Bloom Blumenthal Braverman Brenner
    Brodsky Cantor Chernow Diamond
    Ehrlich Epstein Fein Feinberg
    Feinstein Finkelstein Fishman Forman
    Frankel Fried Friedlander Geller
    Ginsberg Glassman Glazer Goldfarb
    Goldstein Goodman Gordon Gottlieb
    Greenbaum Greenberg Greenfeld Groll
    Gross Grossman Haber Haim
    Halpern Handler Harris Helfand
    Heller Hirsch Hoffman Holtzman
    Horowitz Hyman Jacobs Jaffe
    Kagan Kahn Kantor Kaplan
    Katz Kaufman Kessler Kirsch
    Klein Kline Kohn Kramer
    Kravitz Landau Lasky Lazer
    Lehrer Levin Levine Levinson
    Levy Lieberman Lipman Lipschitz
    Lipschitz London Lowenthal Lurie
    Margolis Markowitz Mayer Meisels
    Meltzer Mendelsohn Meyer Meyers
    Miller Morgenstern Moskowitz Nathan
    Nussbaum Perlman Perlstein Pincus
    Portnoy Rabin Rabinowitz Rappaport
    Resnick Richman Rosen Rosenberg
    Rosenfeld Rosenthal Roth Rothman
    Rubin Rubinstein Sachs Sacks
    Sandler Schapiro Schiff Schneider
    Schwartz Segal Shapiro Sherman
    Siegel Silver Silverberg Silverman
    Singer Spector Spiegel Stein
    Steinberg Stern Strauss Sugarman
    Wasserman Weiner Weinstein Weintraub
    Weiss Weissman Wolf Wolfson
    Zuckerman
    """.split()

    portuguese_add = """
    Silva Santos Oliveira Souza Rodrigues Ferreira Alves Pereira Lima Pinto
    Martins Carvalho Ribeiro Correia Nunes Costa Gomes Mendes
    Barros Teixeira Moreira Cardoso Barbosa Freitas
    Araujo Castro Dias Lopes Machado
    Monteiro Rocha Sousa Andrade
    Cunha Campos Reis Fonseca
    Moura Neves Vieira Miranda
    Coelho Duarte Esteves Faria
    Figueiredo Guimaraes Henriques
    Leal Magalhaes Matos Melo
    Nascimento Pacheco Paiva
    Pinho Queiroz Ramos
    Sampaio Soares Tavares
    Vargas Xavier
    """.split()

    arabic_add = """
    Ahmad Ali Hassan Mohammed Abdullah Ibrahim Mahmoud Omar Yusuf Khalil
    Rashid Said Farid Nasser Hadi Karim Hussein
    Saleh Suleiman Tariq Walid Zaid
    Abbas Abdo Abdul Abdulaziz Abdallah
    Abed Abu Afzal Ahmad Ahmadi
    Akhtar Alami Alawi Alami
    Amin Amiri Anwar Asad
    Ashraf Aslam Atallah Awad
    Aziz Azizi Badawi Bakir
    Barakat Bashir Bassam Bilal
    Boutros Darwish Dawood Diab
    Eid Elamin Fadel Fahd
    Faiz Faraj Farouk Fathi
    Fawzi Fayad Ghani Ghazal
    Habib Hadid Hafez Hakim
    Hamad Hamdan Hamid Hammoud
    Hanif Hariri Hashem Hassan
    Hatim Hijazi Hilal Hussein
    Ibrahim Idris Imran Isa
    Ismail Issa Jabbar Jaber
    Jalal Jamal Jamil Jawad
    Kader Kamal Kamil Karim
    Kassem Kazem Khaled Khalifa
    Khalil Khoury Labib Latif
    Mahdi Mahmoud Majid Malik
    Mansour Marwan Masri Matar
    Mazin Medhat Mehdi Memon
    Mousa Moussa Muhammad Munir
    Murad Musa Mustafa Nabil
    Nader Nadim Nagi Najjar
    Nasr Nasri Nasser Nawaz
    Nizam Noor Nour Obeid
    Omar Osama Othman Qadir
    Qasim Qureshi Rabih Radi
    Rafiq Rahim Rahman Rashad
    Rashid Riad Rizk Saad
    Sabah Saber Sabir Sadek
    Saeed Safi Said Salah
    Salama Saleh Salem Salim
    Sami Samir Sari Sarkis
    Sayed Shaaban Shah Shakir
    Sharif Shehata Siddiqui Suleiman
    Taha Taher Talal Tamer
    Tarek Tawfik Touma Umar
    Usman Wafiq Wahab Wahid
    Waleed Yasin Yasser Younes
    Yousef Youssef Yusuf Zaid
    Zain Zakaria Zaki Zayed
    Ziad Zuhair
    """.split()

    african_extra = {
        "nigerian": """
            Adeyemi Obi Okafor Eze Chukwu Nwosu Uche Okonkwo Ibe
            Adebayo Adebola Adegoke Adekunle Adeniyi
            Adeola Adesina Adetokunbo Adewale
            Adeyemi Agbaje Agu Akinola
            Akintola Alabi Aluko Anya
            Azikiwe Balogun Bello Chibueze
            Chidi Chike Chinedu Chukwuemeka
            Dike Eke Ekwueme Emeka
            Ezeani Ibe Ifeanyi Igwe
            Ike Ikenna Iwobi Jaja
            Kalu Kanu Madu Maduka
            Mba Nduka Nnamani Nwabueze
            Nwankwo Nwafor Nwosu Obasanjo
            Obasi Obinna Obioma Odum
            Ogbonna Ojo Okeke Okoli
            Okonkwo Okoro Okoye Okpara
            Okafor Okon Oladipo Olawale
            Olu Oluwaseun Onyeka Onyema
            Osuji Owoh Uche Ude
            Udo Ugochukwu Umeh Uzoma
        """.split(),
        "ethiopian": """
            Tadesse Bekele Haile Alemayehu Wolde Girma Tesfaye Mekonnen
            Abebe Adane Assefa Ayele
            Berhanu Birhanu Demeke Desta
            Fekadu Gebre Gebremariam Getachew
            Hailu Kebede Lemma Mengistu
            Mulatu Negash Sisay Tadesse
            Tekle Tessema Tsegaye Worku
            Yared Yilma Zewde
        """.split(),
        "ghanaian": """
            Mensah Owusu Boateng Asante Appiah Amoah
            Adjei Agyeman Amoako Ankomah
            Asare Awuah Baffoe Boakye
            Danso Darko Frimpong Gyasi
            Kwarteng Kyei Manu Nkrumah
            Ofori Oppong Osei Owusu
            Prempeh Sarpong Tetteh Yeboah
        """.split(),
        "kenyan": """
            Kamau Otieno Ochieng Wanjiku Mwangi Njeri
            Achieng Adhiambo Anyango Barasa
            Cheruiyot Gathoni Gitau Kariuki
            Kipchoge Kiplagat Kiprop Kiptoo
            Mugo Mutua Muturi Ndungu
            Njoroge Odinga Omondi Onyango
            Owuor Rotich Wambui Wanjiru
        """.split(),
        "somali": """
            Mohamed Abdi Ahmed Ali Hassan Hussein
            Ibrahim Ismail Omar Osman
            Yusuf Aden Farah Gedi
            Jama Muse Nur Said
            Warsame Abukar Dahir Dualeh
            Elmi Garad Haji Hashi
            Ismail Khalif Mahamud
        """.split(),
        "senegalese": """
            Diop Ndiaye Fall Sarr Gueye Ba
            Cisse Diagne Diallo Diouf
            Faye Kane Mbaye Ndour
            Seck Sow Sy Thiam
            Wade
        """.split(),
    }

    # Apply merges
    data["hispanic_surnames"] = merge_list(data.get("hispanic_surnames", []), hispanic_add.split())

    asian = data.get("asian_surnames", {})
    for group, names in asian_extra.items():
        asian[group] = merge_list(asian.get(group, []), names.split() if isinstance(names, str) else names)
    # Drop indian from asian if present
    asian.pop("indian", None)
    data["asian_surnames"] = {k: sorted(v, key=str.lower) for k, v in sorted(asian.items())}

    # Nested indian groups (preserve all flat names in india group already)
    data["indian_surnames"] = {
        k: sorted(v, key=str.lower) for k, v in sorted(indian_groups.items())
    }

    data["african_american_surnames"] = merge_list(
        data.get("african_american_surnames", []), aa_add
    )
    data["native_american_surnames"] = merge_list(
        data.get("native_american_surnames", []), na_add
    )

    european = data.get("european_surnames", {})
    for group, names in european_extra.items():
        european[group] = merge_list(european.get(group, []), names)
    data["european_surnames"] = {k: sorted(v, key=str.lower) for k, v in sorted(european.items())}

    data["jewish_surnames"] = merge_list(data.get("jewish_surnames", []), jewish_add)
    data["portuguese_surnames"] = merge_list(data.get("portuguese_surnames", []), portuguese_add)
    data["arabic_surnames"] = merge_list(data.get("arabic_surnames", []), arabic_add)

    african = data.get("african_surnames", {})
    for region, names in african_extra.items():
        african[region] = merge_list(african.get(region, []), names)
    data["african_surnames"] = {k: sorted(v, key=str.lower) for k, v in sorted(african.items())}

    # Pretty-print compact arrays
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Stats
    def count_list(x):
        if isinstance(x, list):
            return len(x)
        if isinstance(x, dict):
            return sum(len(v) for v in x.values() if isinstance(v, list))
        return 0

    print("Expanded ethnic_names.json")
    for key in data:
        print(f"  {key}: {count_list(data[key])}")


if __name__ == "__main__":
    main()

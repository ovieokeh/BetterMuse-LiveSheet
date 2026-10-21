// Copyright (C) 2026 BetterMuse contributors.
// SPDX-License-Identifier: AGPL-3.0-or-later
package org.audiveris.omr.text;

import org.audiveris.omr.sheet.Sheet;
import java.awt.Rectangle;
import java.awt.geom.Rectangle2D;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Prevent OCR from consuming an explicitly encoded, printed eighth rest.
 * No note, duration or voice is created here. Audiveris still recognizes music.
 */
public final class PrintedRestGuard {
    private PrintedRestGuard() {}

    public static boolean matches(String text, Rectangle2D word, Rectangle2D rest) {
        if (!"7".equals(text) || word.getWidth() <= 0 || word.getHeight() <= 0) return false;
        // The PDF character advance is reliable horizontally. Its trace box is
        // taller than the ink, so require containment, not equal heights.
        double pad = rest.getWidth() * .15;
        return word.getMinX() >= rest.getMinX() - pad
            && word.getMaxX() <= rest.getMaxX() + pad
            && word.getMinY() >= rest.getMinY() - pad
            && word.getMaxY() <= rest.getMaxY() + pad
            && word.getWidth() >= rest.getWidth() * .65
            && word.getHeight() >= rest.getHeight() * .25;
    }

    public static void filter(Sheet sheet, List<TextLine> lines) {
        String file = System.getenv("BETTERMUSE_PRINTED_RESTS");
        if (file == null || sheet == null || lines == null) return;
        try {
            List<Rectangle2D> rests = new ArrayList<>();
            for (String row : Files.readAllLines(Path.of(file))) {
                if (row.startsWith("#") || row.isBlank()) continue;
                String[] fields = row.split("\t");
                if (fields.length != 5) throw new IllegalArgumentException("Invalid printed-rest evidence");
                int page = Integer.parseInt(fields[0]);
                double x = Double.parseDouble(fields[1]), y = Double.parseDouble(fields[2]);
                double w = Double.parseDouble(fields[3]), h = Double.parseDouble(fields[4]);
                if (!(page > 0 && x >= 0 && y >= 0 && w > 0 && h > 0 && x+w <= 1 && y+h <= 1))
                    throw new IllegalArgumentException("Invalid printed-rest bounds");
                if (page == sheet.getStub().getNumber())
                    rests.add(new Rectangle2D.Double(x*sheet.getWidth(), y*sheet.getHeight(),
                        w*sheet.getWidth(), h*sheet.getHeight()));
            }
            for (TextLine line : lines) {
              // OCR can group several rests and ledger lines into one text line.
              // Judge each complete word, preserving all neighboring words.
              for (TextWord word : new ArrayList<>(line.getWords())) {
                Rectangle bounds = word.getBounds();
                long count = rests.stream().filter(r -> matches(word.getValue(), bounds, r)).count();
                if (count == 1) {
                    line.removeWords(List.of(word));
                    System.out.println("BM_PRINTED_REST_OCR page=" + sheet.getStub().getNumber()
                        + " text=7 bounds=" + bounds);
                }
              }
            }
            lines.removeIf(line -> line.getWords().isEmpty());
        } catch (Exception error) {
            throw new IllegalStateException("Printed-rest evidence could not be applied", error);
        }
    }
}
